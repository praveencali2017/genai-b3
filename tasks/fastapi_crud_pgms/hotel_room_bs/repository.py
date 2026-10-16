"""MySQL persistence for bookings. All SQL for the API lives here."""

from collections.abc import Iterator
from enum import StrEnum

import mysql.connector
from mysql.connector.abstracts import MySQLConnectionAbstract

from hotel_room_bs.db_config import load_db_config
from hotel_room_bs.lifecycle import ACTIVE_STATUSES, StatusTransition
from hotel_room_bs.schemas import (
    Booking,
    BookingCreate,
    BookingStatus,
    RoomType,
    RoomUpdate,
)


def _sql_enum(values: type[StrEnum]) -> str:
    """Render an enum's values as a SQL ENUM list, e.g. 'Single', 'Double'."""
    return ", ".join(f"'{member.value}'" for member in values)


# ENUMs come from the same enums the API validates against, so the two can't
# drift. The CHECKs make the database reject bad rows even if validation is
# bypassed.
CREATE_BOOKING_TABLE_SQL = f"""
    CREATE TABLE IF NOT EXISTS hotel_booking (
        id            INT AUTO_INCREMENT PRIMARY KEY,
        customer_name VARCHAR(100)   NOT NULL,
        room_number   INT            NOT NULL CHECK (room_number > 0),
        room_type     ENUM({_sql_enum(RoomType)}) NOT NULL,
        amount        DECIMAL(10, 2) NOT NULL CHECK (amount > 0),
        status        ENUM({_sql_enum(BookingStatus)})
                      NOT NULL DEFAULT '{BookingStatus.BOOKED.value}'
    )
"""

SELECT_COLUMNS = (
    "SELECT id, customer_name, room_number, room_type, amount, status FROM hotel_booking"
)
# Fixed placeholders for the active statuses; the values are bound as params.
ACTIVE_PLACEHOLDERS = ", ".join(["%s"] * len(ACTIVE_STATUSES))


def _to_booking(row: tuple) -> Booking:
    booking_id, customer_name, room_number, room_type, amount, status = row
    return Booking(
        id=booking_id,
        customer_name=customer_name,
        room_number=room_number,
        room_type=room_type,
        amount=float(amount),
        status=status,
    )


class BookingRepository:
    """Booking persistence over one MySQL connection; every write is committed."""

    def __init__(self, connection: MySQLConnectionAbstract) -> None:
        self._connection = connection

    def create_table(self) -> None:
        self._execute_write(CREATE_BOOKING_TABLE_SQL)

    def add(self, booking: BookingCreate) -> Booking:
        with self._connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO hotel_booking (customer_name, room_number, room_type, amount) "
                "VALUES (%s, %s, %s, %s)",
                (
                    booking.customer_name,
                    booking.room_number,
                    booking.room_type.value,
                    booking.amount,
                ),
            )
            new_id = cursor.lastrowid
        self._connection.commit()
        return Booking(id=new_id, status=BookingStatus.BOOKED, **booking.model_dump())

    def list_page(self, limit: int, offset: int) -> list[Booking]:
        with self._connection.cursor() as cursor:
            cursor.execute(
                f"{SELECT_COLUMNS} ORDER BY id LIMIT %s OFFSET %s", (limit, offset)
            )
            return [_to_booking(row) for row in cursor.fetchall()]

    def count(self) -> int:
        with self._connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM hotel_booking")
            (total,) = cursor.fetchone()
            return total

    def get(self, booking_id: int) -> Booking | None:
        with self._connection.cursor() as cursor:
            cursor.execute(f"{SELECT_COLUMNS} WHERE id = %s", (booking_id,))
            row = cursor.fetchone()
        return _to_booking(row) if row else None

    def update_room(self, booking_id: int, changes: RoomUpdate) -> bool:
        """Move an active booking to a new room; return True if a row changed.

        The status check is in the WHERE clause so check-and-change is one
        atomic statement. False means missing, inactive, *or* unchanged
        values, so callers re-read the booking to tell which.
        """
        assignments = changes.model_dump(exclude_none=True, mode="json")
        # Column names come from the RoomUpdate model, never from the client.
        set_clause = ", ".join(f"{column} = %s" for column in assignments)
        affected = self._execute_write(
            f"UPDATE hotel_booking SET {set_clause} "
            f"WHERE id = %s AND status IN ({ACTIVE_PLACEHOLDERS})",
            (
                *assignments.values(),
                booking_id,
                *(status.value for status in ACTIVE_STATUSES),
            ),
        )
        return affected > 0

    def change_status(self, booking_id: int, transition: StatusTransition) -> bool:
        """Apply the transition if the booking is in its from-status.

        Atomic, like update_room: two concurrent check-ins can't both succeed.
        """
        affected = self._execute_write(
            "UPDATE hotel_booking SET status = %s WHERE id = %s AND status = %s",
            (transition.to_status.value, booking_id, transition.from_status.value),
        )
        return affected > 0

    def delete_all(self) -> int:
        return self._execute_write("DELETE FROM hotel_booking")

    def _execute_write(self, sql: str, params: tuple = ()) -> int:
        """Run one write statement, commit it, and return the affected row count."""
        with self._connection.cursor() as cursor:
            cursor.execute(sql, params)
            affected = cursor.rowcount
        self._connection.commit()
        return affected


def connect() -> MySQLConnectionAbstract:
    return mysql.connector.connect(**load_db_config())


def get_repository() -> Iterator[BookingRepository]:
    """FastAPI dependency: a repository on a fresh connection per request.

    mysql-connector connections aren't thread-safe, and FastAPI runs sync
    routes in a threadpool, so requests must not share one connection.
    """
    connection = connect()
    try:
        yield BookingRepository(connection)
    finally:
        connection.close()
