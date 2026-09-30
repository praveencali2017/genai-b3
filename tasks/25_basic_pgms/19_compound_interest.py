principal = float(input("Enter the principal amount: "))
rate = float(input("Enter the annual interest rate (in %): "))
time = float(input("Enter the time in years: "))
compound_frequency = int(input("Enter the number of times interest is compounded per year: "))
# Calculate compound interest
amount = principal * (1 + rate / (100 * compound_frequency)) ** (compound_frequency * time)
compound_interest = amount - principal
print(f"The compound interest for a principal of {principal}, at an annual interest rate of {rate}%, " \
      f"over {time} years with {compound_frequency} compounding periods per year is: {compound_interest}")