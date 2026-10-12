import mysql.connector

from db_config import load_db_config

# Connect to MySQL (settings come from .env)
connection = mysql.connector.connect(**load_db_config())

cursor = connection.cursor()

# Employee Input
name = input("Enter Name: ")
age = int(input("Enter Age: "))
salary = int(input("Enter Salary: "))
desig = input("Enter Designation: ")

# INSERT Query
sql = "INSERT INTO employee(name, age, salary, designation) VALUES (%s, %s, %s, %s)"
values = (name, age, salary, desig)

cursor.execute(sql, values)

connection.commit()

print("Employee Added Successfully...!")

print("-----------------------------------------------------")
cursor.execute("Select * from Employee");

for emp in cursor.fetchall():
    print(emp);
    
    
cursor.close()
connection.close()