principal = float(input("Enter the principal amount: "))
rate = float(input("Enter the annual interest rate (in %): "))
time = float(input("Enter the time in years: "))
# Calculate simple interest
simple_interest = (principal * rate * time) / 100
print(f"The simple interest for a principal of {principal}, at an annual interest rate of {rate}%, " \
      f"over {time} years is: {simple_interest}")