"""Menu-driven Hospital Patient Management System backed by MySQL.

Connects using settings from the project `.env` (via `db_config`),
creates the `patient` table if it does not exist yet, and lets the user
register, display, look up, update, discharge, and delete patients through
a numbered command loop.
"""

import mysql.connector

from db_config import load_db_config

STATUS_ADMITTED = "Admitted"
STATUS_DISCHARGED = "Discharged"

# ENUM + DEFAULT means the database itself rejects any other status,
# and every newly registered patient starts out admitted
CREATE_PATIENT_TABLE_SQL = f"""
    CREATE TABLE IF NOT EXISTS patient (
        id      INT AUTO_INCREMENT PRIMARY KEY,
        name    VARCHAR(100) NOT NULL,
        age     INT NOT NULL,
        disease VARCHAR(200) NOT NULL,
        doctor  VARCHAR(100) NOT NULL,
        status  ENUM('{STATUS_ADMITTED}', '{STATUS_DISCHARGED}')
                NOT NULL DEFAULT '{STATUS_ADMITTED}'
    )
"""

PATIENT_COLUMNS = "id, name, age, disease, doctor, status"

MIN_AGE = 0
MAX_AGE = 150


# ---------- Input helpers ----------

def read_text(prompt: str) -> str:
    """Prompt until the user enters a non-empty string."""
    while True:
        value = input(prompt).strip()
        if value:
            return value
        print("Value cannot be empty. Try again.")


def read_int(prompt: str, minimum: int | None = None) -> int:
    """Prompt until the user enters a whole number (optionally >= minimum)."""
    while True:
        try:
            value = int(input(prompt))
        except ValueError:
            print("Please enter a whole number.")
            continue
        if minimum is not None and value < minimum:
            print(f"Value must be at least {minimum}.")
            continue
        return value


def read_age(prompt: str) -> int:
    """Prompt until the user enters an age between MIN_AGE and MAX_AGE."""
    while True:
        age = read_int(prompt, minimum=MIN_AGE)
        if age <= MAX_AGE:
            return age
        print(f"Age must be at most {MAX_AGE}.")


# ---------- Database actions ----------

def create_patient_table(cursor, connection) -> None:
    """Create the patient table on first run; a no-op if it already exists."""
    cursor.execute(CREATE_PATIENT_TABLE_SQL)
    connection.commit()


def print_patients(patients: list[tuple]) -> None:
    """Print patient rows as an aligned table."""
    print(
        f"{'ID':<5}{'Name':<25}{'Age':>4}  {'Disease':<25}"
        f"{'Doctor':<20}{'Status':<10}"
    )
    print("-" * 91)
    for patient_id, name, age, disease, doctor, status in patients:
        print(
            f"{patient_id:<5}{name:<25}{age:>4}  {disease:<25}"
            f"{doctor:<20}{status:<10}"
        )


def patient_exists(cursor, patient_id: int) -> bool:
    """Return True if a patient with this ID is in the table."""
    cursor.execute("SELECT 1 FROM patient WHERE id = %s", (patient_id,))
    return cursor.fetchone() is not None


def register_patient(cursor, connection) -> None:
    """Prompt for patient details and insert a new row (status defaults to Admitted)."""
    name = read_text("Enter Patient Name: ")
    age = read_age("Enter Age: ")
    disease = read_text("Enter Disease: ")
    doctor = read_text("Enter Doctor Name: ")

    # Parameterized query — values are bound, never concatenated,
    # so user input can't inject SQL
    cursor.execute(
        "INSERT INTO patient (name, age, disease, doctor) VALUES (%s, %s, %s, %s)",
        (name, age, disease, doctor),
    )
    connection.commit()

    print(f"Patient Registered Successfully! (ID: {cursor.lastrowid})")


def display_patients(cursor) -> None:
    """Fetch and print every patient, ordered by ID."""
    cursor.execute(f"SELECT {PATIENT_COLUMNS} FROM patient ORDER BY id")
    patients = cursor.fetchall()

    if patients:
        print_patients(patients)
    else:
        print("No Patients Found!")


def get_patient_by_id(cursor) -> None:
    """Look up a single patient by ID and print it."""
    patient_id = read_int("Enter Patient ID: ")

    cursor.execute(
        f"SELECT {PATIENT_COLUMNS} FROM patient WHERE id = %s",
        (patient_id,),
    )
    patient = cursor.fetchone()

    if patient:
        print_patients([patient])
    else:
        print("Patient Not Found!")


def update_patient(cursor, connection) -> None:
    """Update the diagnosis (disease), the attending doctor, or both.

    Name and age are fixed at registration, and status only changes
    through discharge, so the Admitted -> Discharged transition stays controlled.
    """
    patient_id = read_int("Enter Patient ID: ")

    print("1. Update Diagnosis")
    print("2. Update Doctor")
    print("3. Update Both")
    choice = read_int("Enter Choice: ")

    if choice == 1:
        sql = "UPDATE patient SET disease = %s WHERE id = %s"
        values = (read_text("Enter New Diagnosis: "), patient_id)
    elif choice == 2:
        sql = "UPDATE patient SET doctor = %s WHERE id = %s"
        values = (read_text("Enter New Doctor Name: "), patient_id)
    elif choice == 3:
        sql = "UPDATE patient SET disease = %s, doctor = %s WHERE id = %s"
        values = (
            read_text("Enter New Diagnosis: "),
            read_text("Enter New Doctor Name: "),
            patient_id,
        )
    else:
        print("Invalid Choice!")
        return

    cursor.execute(sql, values)
    connection.commit()

    # rowcount is 0 when no row matched the ID (or the values were unchanged)
    if cursor.rowcount > 0:
        print("Patient Updated Successfully!")
    else:
        print("Patient Not Found or No Changes Made!")


def discharge_patient(cursor, connection) -> None:
    """Mark an admitted patient as discharged; the record is kept."""
    patient_id = read_int("Enter Patient ID: ")

    # The status check lives in the WHERE clause so check-and-change is one
    # atomic statement — two sessions can't both discharge the same patient
    cursor.execute(
        "UPDATE patient SET status = %s WHERE id = %s AND status = %s",
        (STATUS_DISCHARGED, patient_id, STATUS_ADMITTED),
    )
    connection.commit()

    if cursor.rowcount > 0:
        print("Patient Discharged Successfully!")
    elif patient_exists(cursor, patient_id):
        print("Patient Already Discharged!")
    else:
        print("Patient Not Found!")


def delete_patient(cursor, connection) -> None:
    """Delete a patient record by ID."""
    patient_id = read_int("Enter Patient ID: ")

    cursor.execute("DELETE FROM patient WHERE id = %s", (patient_id,))
    connection.commit()

    if cursor.rowcount > 0:
        print("Patient Record Deleted Successfully!")
    else:
        print("Patient Not Found!")


def delete_all_patients(cursor, connection) -> None:
    """Delete every patient record after an explicit confirmation."""
    confirm = input("Delete ALL patient records? This cannot be undone (y/n): ")
    if confirm.strip().lower() != "y":
        print("Cancelled.")
        return

    cursor.execute("DELETE FROM patient")
    connection.commit()

    print(f"Deleted {cursor.rowcount} Patient Record(s).")


# ---------- Menu loop ----------

def main() -> None:
    """Run the interactive hospital-management menu until the user exits.

    The patient table is created first if needed. The connection is
    always closed afterwards, even if an error occurs.
    """
    connection = mysql.connector.connect(**load_db_config())
    cursor = connection.cursor()

    try:
        create_patient_table(cursor, connection)

        while True:
            print("\n====== HOSPITAL PATIENT MANAGEMENT ======")
            print("1. Register Patient")
            print("2. Display All Patients")
            print("3. Search Patient By ID")
            print("4. Update Diagnosis / Doctor")
            print("5. Discharge Patient")
            print("6. Delete Patient Record")
            print("7. Delete All Records")
            print("8. Exit")
            choice = read_int("Enter Choice: ")

            try:
                if choice == 1:
                    register_patient(cursor, connection)
                elif choice == 2:
                    display_patients(cursor)
                elif choice == 3:
                    get_patient_by_id(cursor)
                elif choice == 4:
                    update_patient(cursor, connection)
                elif choice == 5:
                    discharge_patient(cursor, connection)
                elif choice == 6:
                    delete_patient(cursor, connection)
                elif choice == 7:
                    delete_all_patients(cursor, connection)
                elif choice == 8:
                    print("Thank You!")
                    break
                else:
                    print("Invalid Choice!")
            except mysql.connector.Error as error:
                # Undo the failed statement, report it, and keep the menu running
                connection.rollback()
                print("Database Error:", error)
    finally:
        # Always release DB resources, even if the loop exits via an error
        cursor.close()
        connection.close()


if __name__ == "__main__":
    main()
