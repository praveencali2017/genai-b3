consumed_units = float(input("Enter the number of units consumed: "))
# Define the rates for different slabs
if consumed_units <= 100:
    bill_amount = consumed_units * 5
elif consumed_units <= 200:
    bill_amount = 500 + (consumed_units - 100) * 7
else:
    # for units above 200, let's assume a rate of 10 per unit
    bill_amount = 500 + 700 + (consumed_units - 200) * 10
print(f"The total electric bill for {consumed_units} units is: {bill_amount}")