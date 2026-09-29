alpha = input("Enter a character: ").lower()
if len(alpha) == 1 and alpha.isalpha():
    if alpha in 'aeiou':
        print(alpha, "is a vowel.")
    else:
        print(alpha, "is a consonant.")
else:
    print("Please enter a single alphabetic character.")