length = int(input("Enter the length of the rectangle: "))
width = int(input("Enter the width of the rectangle: "))
if length <= 0 or width <= 0:
    print("Length and width must be positive numbers.")
else:
    # Calculate area and perimeter
    area = length * width
    perimeter = 2 * (length + width)
    print("Area of the rectangle is:", area)
    print("Perimeter of the rectangle is:", perimeter)
