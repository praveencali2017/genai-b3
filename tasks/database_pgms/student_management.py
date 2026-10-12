"""Menu-driven Student Management System backed by MySQL.

Connects using settings from the project `.env` (via `db_config`),
creates the `student` table if it does not exist yet, and lets the user
add, display, look up, update, and delete student records through a
numbered command loop.
"""

import mysql.connector

from db_config import load_db_config

CREATE_STUDENT_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS student (
        id     INT AUTO_INCREMENT PRIMARY KEY,
        name   VARCHAR(100) NOT NULL,
        age    INT NOT NULL,
        course VARCHAR(100) NOT NULL,
        marks  DECIMAL(5, 2) NOT NULL
    )
"""

MIN_MARKS = 0.0
MAX_MARKS = 100.0


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


def read_marks(prompt: str) -> float:
    """Prompt until the user enters marks within the allowed range."""
    while True:
        try:
            value = float(input(prompt))
        except ValueError:
            print("Please enter a number.")
            continue
        if not MIN_MARKS <= value <= MAX_MARKS:
            print(f"Marks must be between {MIN_MARKS:g} and {MAX_MARKS:g}.")
            continue
        return value


# ---------- Database actions ----------

def create_student_table(cursor, connection) -> None:
    """Create the student table on first run; a no-op if it already exists."""
    cursor.execute(CREATE_STUDENT_TABLE_SQL)
    connection.commit()


def print_students(students: list[tuple]) -> None:
    """Print student rows as an aligned table."""
    print(f"{'ID':<5}{'Name':<20}{'Age':<6}{'Course':<20}{'Marks':>7}")
    print("-" * 58)
    for student_id, name, age, course, marks in students:
        print(f"{student_id:<5}{name:<20}{age:<6}{course:<20}{marks:>7}")


def add_student(cursor, connection) -> None:
    """Prompt for student details and insert a new row."""
    name = read_text("Enter Name: ")
    age = read_int("Enter Age: ", minimum=1)
    course = read_text("Enter Course: ")
    marks = read_marks("Enter Marks: ")

    # Parameterized query — values are bound, never concatenated,
    # so user input can't inject SQL
    cursor.execute(
        "INSERT INTO student (name, age, course, marks) VALUES (%s, %s, %s, %s)",
        (name, age, course, marks),
    )
    connection.commit()

    print(f"Student Added Successfully! (ID: {cursor.lastrowid})")


def display_students(cursor) -> None:
    """Fetch and print every student, ordered by ID."""
    cursor.execute("SELECT id, name, age, course, marks FROM student ORDER BY id")
    students = cursor.fetchall()

    if students:
        print_students(students)
    else:
        print("No Students Found!")


def get_student_by_id(cursor) -> None:
    """Look up a single student by ID and print it."""
    student_id = read_int("Enter Student ID: ")

    cursor.execute(
        "SELECT id, name, age, course, marks FROM student WHERE id = %s",
        (student_id,),
    )
    student = cursor.fetchone()

    if student:
        print_students([student])
    else:
        print("Student Not Found!")


def update_student(cursor, connection) -> None:
    """Update the marks, the course, or both for a student."""
    student_id = read_int("Enter Student ID: ")

    print("1. Update Marks")
    print("2. Update Course")
    print("3. Update Both")
    choice = read_int("Enter Choice: ")

    if choice == 1:
        sql = "UPDATE student SET marks = %s WHERE id = %s"
        values = (read_marks("Enter New Marks: "), student_id)
    elif choice == 2:
        sql = "UPDATE student SET course = %s WHERE id = %s"
        values = (read_text("Enter New Course: "), student_id)
    elif choice == 3:
        sql = "UPDATE student SET course = %s, marks = %s WHERE id = %s"
        values = (
            read_text("Enter New Course: "),
            read_marks("Enter New Marks: "),
            student_id,
        )
    else:
        print("Invalid Choice!")
        return

    cursor.execute(sql, values)
    connection.commit()

    # rowcount is 0 when no row matched the ID (or the values were unchanged)
    if cursor.rowcount > 0:
        print("Student Updated Successfully!")
    else:
        print("Student Not Found or No Changes Made!")


def delete_student(cursor, connection) -> None:
    """Delete a student row by ID."""
    student_id = read_int("Enter Student ID: ")

    cursor.execute("DELETE FROM student WHERE id = %s", (student_id,))
    connection.commit()

    if cursor.rowcount > 0:
        print("Student Deleted Successfully!")
    else:
        print("Student Not Found!")


def delete_all_students(cursor, connection) -> None:
    """Delete every student after an explicit confirmation."""
    confirm = input("Delete ALL students? This cannot be undone (y/n): ")
    if confirm.strip().lower() != "y":
        print("Cancelled.")
        return

    cursor.execute("DELETE FROM student")
    connection.commit()

    print(f"Deleted {cursor.rowcount} Student(s).")


# ---------- Menu loop ----------

def main() -> None:
    """Run the interactive student-management menu until the user exits.

    The student table is created first if needed. The connection is
    always closed afterwards, even if an error occurs.
    """
    connection = mysql.connector.connect(**load_db_config())
    cursor = connection.cursor()

    try:
        create_student_table(cursor, connection)

        while True:
            print("\n====== STUDENT MANAGEMENT ======")
            print("1. Add Student")
            print("2. Display All Students")
            print("3. Get Student By ID")
            print("4. Update Marks/Course")
            print("5. Delete Student")
            print("6. Delete All Students")
            print("7. Exit")
            choice = read_int("Enter Choice: ")

            try:
                if choice == 1:
                    add_student(cursor, connection)
                elif choice == 2:
                    display_students(cursor)
                elif choice == 3:
                    get_student_by_id(cursor)
                elif choice == 4:
                    update_student(cursor, connection)
                elif choice == 5:
                    delete_student(cursor, connection)
                elif choice == 6:
                    delete_all_students(cursor, connection)
                elif choice == 7:
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
