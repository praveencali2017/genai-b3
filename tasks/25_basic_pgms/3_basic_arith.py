num1 = int(input("Enter first number: "))
num2 = int(input("Enter second number: "))
if num2 == 0:
    print("Error: Division by zero is not allowed.")
else:
    sum = num1 + num2
    sub = num1 - num2
    mul = num1 * num2
    div = num1 / num2
    rem = num1 % num2
    print("The sum of", num1, "and", num2, "is:", sum)
    print("The difference of", num1, "and", num2, "is:", sub)
    print("The product of", num1, "and", num2, "is:", mul)
    print("The quotient of", num1, "and", num2, "is:", div)
    print("The remainder of", num1, "and", num2, "is:", rem)