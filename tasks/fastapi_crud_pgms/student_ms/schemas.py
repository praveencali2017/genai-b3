"""Request and response models for the Student Management API."""

from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Limits mirror the `student` table columns (VARCHAR(100), DECIMAL(5, 2)).
Name = Annotated[str, Field(min_length=1, max_length=100)]
Course = Annotated[str, Field(min_length=1, max_length=100)]
Age = Annotated[int, Field(ge=1)]
Marks = Annotated[float, Field(ge=0, le=100)]


class StudentCreate(BaseModel):
    """Fields a client supplies when adding a student."""

    model_config = ConfigDict(str_strip_whitespace=True)

    name: Name
    age: Age
    course: Course
    marks: Marks


class StudentUpdate(BaseModel):
    """A change to a student's course, marks, or both."""

    model_config = ConfigDict(str_strip_whitespace=True)

    course: Course | None = None
    marks: Marks | None = None

    @model_validator(mode="after")
    def require_a_change(self) -> Self:
        if self.course is None and self.marks is None:
            raise ValueError("Provide at least one of 'course' or 'marks'.")
        return self


class Student(StudentCreate):
    """A stored student, including its database-assigned id."""

    id: int


class StudentPage(BaseModel):
    """One page of students plus the total, so clients know if more exist."""

    items: list[Student]
    total: int
    limit: int
    offset: int


class DeleteAllResult(BaseModel):
    deleted: int
