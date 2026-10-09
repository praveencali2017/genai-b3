import mysql.connector

from db_config import load_db_config

# MySQL Connection (settings come from .env)
connection = mysql.connector.connect(**load_db_config())

cursor = connection.cursor()


# Menu Driven Application
while True:

    print("\n====== EMPLOYEE MANAGEMENT ======")
    print("1. Add Employee")
    print("2. Display Employees")
    print("3. Search Employee")
    print("4. Update Employee")
    print("5. Delete Employee")
    print("6. Exit")
    try:
        choice = int(input("Enter Choice: "))
        # INSERT
        if choice == 1:
            name = input("Enter Name: ")
            age = int(input("Enter Age: "))
            salary = int(input("Enter Salary: "))
            desig = input("Enter Designation: ")

            sql = """INSERT INTO employee
                     (name, age, salary, designation)
                     VALUES (%s, %s, %s, %s)"""

            cursor.execute(sql, (name, age, salary, desig))
            connection.commit()

            print("Employee Added Successfully!")

        # DISPLAY
        elif choice == 2:
            cursor.execute("SELECT * FROM employee")

            employees = cursor.fetchall()

            if employees:
                for emp in employees:
                    print(emp)
            else:
                print("No Employees Found!")

        # SEARCH
        elif choice == 3:
            id = int(input("Enter Employee ID: "))

            cursor.execute(
                "SELECT * FROM employee WHERE id=%s", (id,)
            )

            emp = cursor.fetchone()

            if emp:
                print(emp)
            else:
                print("Employee Not Found!")

        # UPDATE
        elif choice == 4:
            id = int(input("Enter Employee ID: "))
            name = input("Enter New Name: ")
            age = int(input("Enter New Age: "))
            salary = int(input("Enter New Salary: "))
            desig = input("Enter New Designation: ")

            sql = """UPDATE employee
                     SET name=%s, age=%s, salary=%s, designation=%s
                     WHERE id=%s"""

            cursor.execute(
                sql, (name, age, salary, desig, id)
            )

            connection.commit()

            if cursor.rowcount > 0:
                print("Employee Updated Successfully!")
            else:
                print("Employee Not Found!")

        # DELETE
        elif choice == 5:
            id = int(input("Enter Employee ID: "))

            cursor.execute(
                "DELETE FROM employee WHERE id=%s", (id,)
            )

            connection.commit()

            if cursor.rowcount > 0:
                print("Employee Deleted Successfully!")
            else:
                print("Employee Not Found!")

        # EXIT
        elif choice == 6:
            print("Thank You!")
            break

        else:
            print("Invalid Choice!")
    except ValueError:
        print("Invalid Input! Enter a valid number.")
    except mysql.connector.Error as error:
        connection.rollback()
        print("Database Error:", error)

cursor.close()
connection.close()