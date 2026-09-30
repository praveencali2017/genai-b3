subjects = []
total_marks = 0
TOTAL_SUBJECTS = 5
for i in range(TOTAL_SUBJECTS):
    subject = input(f"Enter the name of subject {i + 1}: ")
    marks = float(input(f"Enter the marks obtained in {subject}: "))
    while marks < 0 or marks > 100:
        print("Invalid marks. Please enter marks between 0 and 100.")
        marks = float(input(f"Enter the marks obtained in {subject}: "))
    grade = ''
    total_marks += marks
    if marks >= 90:
        grade = 'A'
    elif marks >= 80:
        grade = 'B'
    elif marks >= 70:
        grade = 'C'
    elif marks >= 60:
        grade = 'D'
    else:
        grade = 'F'
    subjects.append((subject, marks, grade))
percentage = (total_marks / (TOTAL_SUBJECTS * 100)) * 100
overall_grade = ''
if percentage >= 90:
    overall_grade = 'A'
elif percentage >= 80:
    overall_grade = 'B'
elif percentage >= 70:
    overall_grade = 'C'
elif percentage >= 60:
    overall_grade = 'D'
else:
    overall_grade = 'F'
print("\nStudent's Result:")
for subject, marks, grade in subjects:
    print(f"{subject}: Marks = {marks}, Grade = {grade}")
print(f"Total Marks: {total_marks}/{TOTAL_SUBJECTS * 100}")
print(f"Percentage: {percentage:.2f}%")
print(f"Overall Grade: {overall_grade}")
