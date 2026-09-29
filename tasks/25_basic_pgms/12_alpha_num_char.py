char = input("Enter a character: ")
if len(char) != 1:
    print("Please enter a single character.")
elif char.isalpha():
    print(char, "is an alphabetic character.")
elif char.isdigit():
    print(char, "is a numeric character.")
else:
    print(char, "is a special character.")