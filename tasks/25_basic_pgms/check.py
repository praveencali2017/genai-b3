class Animal:
    eyes = 2
    def eyes_count(self):
        return self.eyes

a1 = Animal()
print(a1.eyes_count())  # Output: 2
a1.eyes = 3
a2 = Animal()
print(a1.eyes_count())  # Output: 3
print(a2.eyes_count())  # Output: 2