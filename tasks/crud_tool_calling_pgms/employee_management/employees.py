"""Employee records: Pydantic schemas and the MySQL repository."""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from typing import Annotated, Any

import mysql.connector
from mysql.connector.constants import ClientFlag
from mysql.connector.cursor import MySQLCursorDict
from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    EmailStr,
    Field,
    StringConstraints,
)

# ---------------------------------------------------------------- schemas

MAX_TEXT_LENGTH = 100
# DECIMAL(12, 2): the largest value the salary column can store.
MAX_SALARY = 9_999_999_999.99
EARLIEST_HIRE_DATE = date(1900, 1, 1)


def _latest_hire_date(today: date) -> date:
    """One year from today: allows upcoming hires but catches typos like 2206."""
    try:
        return today.replace(year=today.year + 1)
    except ValueError:  # 29 February
        return today.replace(year=today.year + 1, day=28)


def _check_hire_date(value: date) -> date:
    latest = _latest_hire_date(date.today())
    if not EARLIEST_HIRE_DATE <= value <= latest:
        raise ValueError(f"must be between {EARLIEST_HIRE_DATE} and {latest}")
    return value


def _strip(value: Any) -> Any:
    return value.strip() if isinstance(value, str) else value


# Surrounding whitespace is stripped before the length check, so "   " is rejected.
TextField = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_TEXT_LENGTH),
]
EmailField = Annotated[EmailStr, BeforeValidator(_strip)]
SalaryField = Annotated[float, Field(ge=0, le=MAX_SALARY, allow_inf_nan=False)]
HireDateField = Annotated[date, AfterValidator(_check_hire_date)]


class EmployeeCreate(BaseModel):
    # Unknown fields (e.g. "phone") are rejected rather than silently dropped, so
    # neither the form nor the chatbot can report a change that was never stored.
    model_config = ConfigDict(extra="forbid")

    name: TextField
    email: EmailField
    department: TextField
    position: TextField
    salary: SalaryField
    hire_date: HireDateField | None = None


class EmployeeUpdate(BaseModel):
    """Partial update: only the fields that are set are changed."""

    model_config = ConfigDict(extra="forbid")

    name: TextField | None = None
    email: EmailField | None = None
    department: TextField | None = None
    position: TextField | None = None
    salary: SalaryField | None = None
    hire_date: HireDateField | None = None


class Employee(BaseModel):
    """A stored employee as read back from the database. Input rules live on
    EmployeeCreate/EmployeeUpdate only, so tightening them can never make
    existing rows unreadable."""

    id: int
    name: str
    email: str
    department: str
    position: str
    salary: float
    hire_date: date | None = None


# ---------------------------------------------------------------- repository


class EmployeeNotFoundError(LookupError):
    def __init__(self, employee_id: int) -> None:
        super().__init__(f"Employee {employee_id} not found.")


class DuplicateEmailError(ValueError):
    def __init__(self, email: str) -> None:
        super().__init__(f"An employee with email {email} already exists.")


REQUIRED_DB_SETTINGS = ("DB_HOST", "DB_USER", "DB_PASSWORD", "DB_NAME")
DEFAULT_DB_PORT = 3306
MYSQL_DUPLICATE_KEY = 1062
EMPLOYEE_COLUMNS = "id, name, email, department, position, salary, hire_date"


def load_db_config() -> dict[str, Any]:
    """Connection kwargs from .env; fails at startup naming every missing setting."""
    missing = [name for name in REQUIRED_DB_SETTINGS if not os.getenv(name, "").strip()]
    if missing:
        raise RuntimeError(f"Missing MySQL settings in .env: {', '.join(missing)}.")
    raw_port = os.getenv("DB_PORT", "").strip()
    try:
        port = int(raw_port) if raw_port else DEFAULT_DB_PORT
    except ValueError:
        raise RuntimeError(
            f"DB_PORT must be a whole number, got {raw_port!r}."
        ) from None
    return {
        "host": os.environ["DB_HOST"],
        "port": port,
        "user": os.environ["DB_USER"],
        "password": os.environ["DB_PASSWORD"],
        "database": os.environ["DB_NAME"],
        # Make UPDATE's rowcount mean "rows matched", not "rows changed", so saving
        # unchanged values isn't mistaken for a missing employee.
        "client_flags": [ClientFlag.FOUND_ROWS],
    }


@contextmanager
def connect() -> Iterator[MySQLCursorDict]:
    """One short-lived connection per operation: commit on success, roll back on error."""
    connection = mysql.connector.connect(**load_db_config())
    try:
        with connection.cursor(dictionary=True) as cursor:
            yield cursor
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_db() -> None:
    """Create the database (if the user may) and the employees table."""
    config = load_db_config()
    database = config.pop("database")
    with mysql.connector.connect(**config) as server, server.cursor() as cursor:
        cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{database}`")
    with connect() as cursor:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS employees (
                id INT AUTO_INCREMENT PRIMARY KEY,
                name VARCHAR(100) NOT NULL,
                email VARCHAR(255) NOT NULL UNIQUE,
                department VARCHAR(100) NOT NULL,
                position VARCHAR(100) NOT NULL,
                salary DECIMAL(12, 2) NOT NULL,
                hire_date DATE NULL
            )
            """
        )


def _is_duplicate_key(error: mysql.connector.IntegrityError) -> bool:
    return error.errno == MYSQL_DUPLICATE_KEY


def list_employees(search: str | None = None) -> list[Employee]:
    query = f"SELECT {EMPLOYEE_COLUMNS} FROM employees"
    params: tuple[str, ...] = ()
    if search:
        query += " WHERE name LIKE %s OR email LIKE %s OR department LIKE %s OR position LIKE %s"
        params = (f"%{search}%",) * 4
    with connect() as cursor:
        cursor.execute(query + " ORDER BY id", params)
        rows = cursor.fetchall()
    return [Employee(**row) for row in rows]


def get_employee(employee_id: int) -> Employee:
    with connect() as cursor:
        cursor.execute(
            f"SELECT {EMPLOYEE_COLUMNS} FROM employees WHERE id = %s", (employee_id,)
        )
        row = cursor.fetchone()
    if row is None:
        raise EmployeeNotFoundError(employee_id)
    return Employee(**row)


def create_employee(data: EmployeeCreate) -> Employee:
    try:
        with connect() as cursor:
            cursor.execute(
                "INSERT INTO employees (name, email, department, position, salary, hire_date)"
                " VALUES (%s, %s, %s, %s, %s, %s)",
                (
                    data.name,
                    data.email,
                    data.department,
                    data.position,
                    data.salary,
                    data.hire_date,
                ),
            )
            new_id = cursor.lastrowid
    except mysql.connector.IntegrityError as error:
        if _is_duplicate_key(error):
            raise DuplicateEmailError(data.email) from None
        raise
    return Employee(id=new_id, **data.model_dump())


def update_employee(employee_id: int, changes: EmployeeUpdate) -> Employee:
    fields = changes.model_dump(exclude_unset=True)
    if not fields:
        return get_employee(employee_id)
    # Column names come from the EmployeeUpdate model, never from user input.
    assignments = ", ".join(f"{column} = %s" for column in fields)
    try:
        with connect() as cursor:
            cursor.execute(
                f"UPDATE employees SET {assignments} WHERE id = %s",
                (*fields.values(), employee_id),
            )
            matched = cursor.rowcount
    except mysql.connector.IntegrityError as error:
        if _is_duplicate_key(error):
            raise DuplicateEmailError(str(fields.get("email"))) from None
        raise
    if matched == 0:
        raise EmployeeNotFoundError(employee_id)
    return get_employee(employee_id)


def delete_employee(employee_id: int) -> None:
    with connect() as cursor:
        cursor.execute("DELETE FROM employees WHERE id = %s", (employee_id,))
        deleted = cursor.rowcount
    if deleted == 0:
        raise EmployeeNotFoundError(employee_id)
