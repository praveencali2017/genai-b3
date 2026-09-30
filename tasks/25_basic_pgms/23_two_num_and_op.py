num1 = int(input("Enter first number: "))
num2 = int(input("Enter second number: "))
operation = input("Enter the operation (+, -, *, /, %): ")
if operation == '+':
    result = num1 + num2
    print("The sum of", num1, "and", num2, "is:", result)
elif operation == '-':
    result = num1 - num2
    print("The difference of", num1, "and", num2, "is:", result)
elif operation == '*':
    result = num1 * num2
    print("The product of", num1, "and", num2, "is:", result)
elif operation == '/':
    if num2 != 0:
        result = num1 / num2
        print("The quotient of", num1, "and", num2, "is:", result)
    else:
        print("Error: Division by zero is not allowed.")
elif operation == '%':
    if num2 != 0:
        result = num1 % num2
        print("The remainder of", num1, "and", num2, "is:", result)
    else:
        print("Error: Division by zero is not allowed.")
else:
    print("Invalid operation. Please enter one of +, -, *, /, %.")
