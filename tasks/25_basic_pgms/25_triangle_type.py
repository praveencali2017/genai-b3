side1 = int(input("Enter the length of the first side of the triangle: "))
side2 = int(input("Enter the length of the second side of the triangle: "))
side3 = int(input("Enter the length of the third side of the triangle: "))
# Check if the triangle is valid first
if (side1 + side2 > side3) and (side1 + side3 > side2) and (side2 + side3 > side1):
    # Determine the type of triangle
    if side1 == side2 == side3:
        print("The triangle is equilateral.")
    elif side1 == side2 or side1 == side3 or side2 == side3:
        print("The triangle is isosceles.")
    else:
        print("The triangle is scalene.")
else:
    print("The given sides cannot form a valid triangle.") 