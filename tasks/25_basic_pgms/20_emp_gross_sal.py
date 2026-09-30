basic_salary = float(input("Enter the basic salary of the employee: "))
while basic_salary < 0:
    print("Basic salary cannot be negative. Please enter a valid amount.")
    basic_salary = float(input("Enter the basic salary of the employee: "))
# Calculate gross salary based on basic salary
if basic_salary <= 10000:
    hra = 0.2 * basic_salary  # House Rent Allowance
    da = 0.8 * basic_salary   # Dearness Allowance
elif basic_salary <= 20000:
    hra = 0.25 * basic_salary
    da = 0.9 * basic_salary
else:
    hra = 0.3 * basic_salary
    da = 0.95 * basic_salary
gross_salary = basic_salary + hra + da
print("Gross salary of the employee is:", gross_salary)