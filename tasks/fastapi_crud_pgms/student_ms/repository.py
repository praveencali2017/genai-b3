"""MySQL persistence for students. All SQL for the API lives here."""

from collections.abc import Iterator

import mysql.connector
from mysql.connector.abstracts import MySQLConnectionAbstract

from student_ms.db_config import load_db_config
from student_ms.schemas import Student, StudentCreate, StudentUpdate

# Same table as tasks/database_pgms/student_management.py, so both programs
# share the data.
CREATE_STUDENT_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS student (
        id     INT AUTO_INCREMENT PRIMARY KEY,
        name   VARCHAR(100) NOT NULL,
        age    INT NOT NULL,
        course VARCHAR(100) NOT NULL,
        marks  DECIMAL(5, 2) NOT NULL
    )
"""

SELECT_COLUMNS = "SELECT id, name, age, course, marks FROM student"


def _to_student(row: tuple) -> Student:
    student_id, name, age, course, marks = row
    return Student(id=student_id, name=name, age=age, course=course, marks=float(marks))


class StudentRepository:
    """Student CRUD over one MySQL connection; every write is committed."""

    def __init__(self, connection: MySQLConnectionAbstract) -> None:
        self._connection = connection

    def create_table(self) -> None:
        self._execute_write(CREATE_STUDENT_TABLE_SQL)

    def add(self, student: StudentCreate) -> Student:
        with self._connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO student (name, age, course, marks) VALUES (%s, %s, %s, %s)",
                (student.name, student.age, student.course, student.marks),
            )
            new_id = cursor.lastrowid
        self._connection.commit()
        return Student(id=new_id, **student.model_dump())

    def list_page(self, limit: int, offset: int) -> list[Student]:
        with self._connection.cursor() as cursor:
            cursor.execute(
                f"{SELECT_COLUMNS} ORDER BY id LIMIT %s OFFSET %s", (limit, offset)
            )
            return [_to_student(row) for row in cursor.fetchall()]

    def count(self) -> int:
        with self._connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM student")
            (total,) = cursor.fetchone()
            return total

    def get(self, student_id: int) -> Student | None:
        with self._connection.cursor() as cursor:
            cursor.execute(f"{SELECT_COLUMNS} WHERE id = %s", (student_id,))
            row = cursor.fetchone()
        return _to_student(row) if row else None

    def update(self, student_id: int, changes: StudentUpdate) -> Student | None:
        """Apply the given changes; return the updated student, or None if missing.

        The row is re-read afterwards because rowcount is 0 both when the id
        doesn't exist and when the new values equal the old ones.
        """
        assignments = changes.model_dump(exclude_none=True)
        # Column names come from the StudentUpdate model, never from the client.
        set_clause = ", ".join(f"{column} = %s" for column in assignments)
        self._execute_write(
            f"UPDATE student SET {set_clause} WHERE id = %s",
            (*assignments.values(), student_id),
        )
        return self.get(student_id)

    def delete(self, student_id: int) -> bool:
        return self._execute_write("DELETE FROM student WHERE id = %s", (student_id,)) > 0

    def delete_all(self) -> int:
        return self._execute_write("DELETE FROM student")

    def _execute_write(self, sql: str, params: tuple = ()) -> int:
        """Run one write statement, commit it, and return the affected row count."""
        with self._connection.cursor() as cursor:
            cursor.execute(sql, params)
            affected = cursor.rowcount
        self._connection.commit()
        return affected


def connect() -> MySQLConnectionAbstract:
    return mysql.connector.connect(**load_db_config())


def get_repository() -> Iterator[StudentRepository]:
    """FastAPI dependency: a repository on a fresh connection per request.

    mysql-connector connections aren't thread-safe, and FastAPI runs sync
    routes in a threadpool, so requests must not share one connection.
    """
    connection = connect()
    try:
        yield StudentRepository(connection)
    finally:
        connection.close()
