side1 = int(input("Enter the length of the first side of the triangle: "))
side2 = int(input("Enter the length of the second side of the triangle: "))
side3 = int(input("Enter the length of the third side of the triangle: "))
# Check if the sum of any two sides is greater than the third side
if (side1 + side2 > side3) and (side1 + side3 > side2) and (side2 + side3 > side1):
    print("The given sides can form a valid triangle.")
else:
    print("The given sides cannot form a valid triangle.")