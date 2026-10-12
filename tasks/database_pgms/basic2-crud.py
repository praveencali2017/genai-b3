"""Menu-driven CRUD application for the `employee` table in MySQL.

Connects using settings from the project `.env` (via `db_config`) and
lets the user add, display, search, update, and delete employee
records through a numbered command loop.
"""

import mysql.connector

from db_config import load_db_config


def add_employee(cursor, connection) -> None:
    """Prompt for employee details and insert a new row."""
    name = input("Enter Name: ")
    age = int(input("Enter Age: "))
    salary = int(input("Enter Salary: "))
    desig = input("Enter Designation: ")

    # Parameterized query — values are bound, never concatenated,
    # so user input can't inject SQL
    sql = """INSERT INTO employee
             (name, age, salary, designation)
             VALUES (%s, %s, %s, %s)"""

    cursor.execute(sql, (name, age, salary, desig))
    connection.commit()  # persist the insert to the database

    print("Employee Added Successfully!")


def display_employees(cursor) -> None:
    """Fetch and print every row in the employee table."""
    cursor.execute("SELECT * FROM employee")
    employees = cursor.fetchall()

    if employees:
        for emp in employees:
            print(emp)
    else:
        print("No Employees Found!")


def search_employee(cursor) -> None:
    """Look up a single employee by their ID and print it."""
    # `emp_id` instead of `id` to avoid shadowing the Python builtin
    emp_id = int(input("Enter Employee ID: "))

    cursor.execute(
        "SELECT * FROM employee WHERE id=%s", (emp_id,)
    )

    emp = cursor.fetchone()  # at most one row expected

    if emp:
        print(emp)
    else:
        print("Employee Not Found!")


def update_employee(cursor, connection) -> None:
    """Update every field of an employee identified by their ID."""
    emp_id = int(input("Enter Employee ID: "))
    name = input("Enter New Name: ")
    age = int(input("Enter New Age: "))
    salary = int(input("Enter New Salary: "))
    desig = input("Enter New Designation: ")

    sql = """UPDATE employee
             SET name=%s, age=%s, salary=%s, designation=%s
             WHERE id=%s"""

    cursor.execute(sql, (name, age, salary, desig, emp_id))
    connection.commit()

    # rowcount reports how many rows the UPDATE actually affected
    if cursor.rowcount > 0:
        print("Employee Updated Successfully!")
    else:
        print("Employee Not Found!")


def delete_employee(cursor, connection) -> None:
    """Delete an employee row by its ID."""
    emp_id = int(input("Enter Employee ID: "))

    cursor.execute(
        "DELETE FROM employee WHERE id=%s", (emp_id,)
    )

    connection.commit()

    if cursor.rowcount > 0:
        print("Employee Deleted Successfully!")
    else:
        print("Employee Not Found!")


def main() -> None:
    """Run the interactive employee-management menu until the user exits.

    The connection is opened here (settings come from the `.env` file)
    and always closed afterwards, even if an error occurs.
    """
    connection = mysql.connector.connect(**load_db_config())
    cursor = connection.cursor()

    try:
        while True:
            print("\n====== EMPLOYEE MANAGEMENT ======")
            print("1. Add Employee")
            print("2. Display Employees")
            print("3. Search Employee")
            print("4. Update Employee")
            print("5. Delete Employee")
            print("6. Exit")
            choice = int(input("Enter Choice: "))

            if choice == 1:
                add_employee(cursor, connection)
            elif choice == 2:
                display_employees(cursor)
            elif choice == 3:
                search_employee(cursor)
            elif choice == 4:
                update_employee(cursor, connection)
            elif choice == 5:
                delete_employee(cursor, connection)
            elif choice == 6:
                print("Thank You!")
                break
            else:
                print("Invalid Choice!")
    except ValueError:
        # Non-numeric input from the user (e.g. choice, age, salary)
        print("Invalid Input! Enter a valid number.")
    except mysql.connector.Error as error:
        # Undo the failed statement, then report the DB error
        connection.rollback()
        print("Database Error:", error)
    finally:
        # Always release DB resources, even if the loop exits via an error
        cursor.close()
        connection.close()


if __name__ == "__main__":
    main()
