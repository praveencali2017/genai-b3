"""Menu-driven Hotel Room Booking System backed by MySQL.

Connects using settings from the project `.env` (via `db_config`),
creates the `booking` table if it does not exist yet, and lets the user
create, display, look up, re-room, check in, check out, cancel, and delete
bookings through a numbered command loop.
"""

import mysql.connector

from db_config import load_db_config

STATUS_BOOKED = "Booked"
STATUS_CHECKED_IN = "Checked In"
STATUS_CHECKED_OUT = "Checked Out"
STATUS_CANCELLED = "Cancelled"
BOOKING_STATUSES = (STATUS_BOOKED, STATUS_CHECKED_IN, STATUS_CHECKED_OUT, STATUS_CANCELLED)

ROOM_TYPES = ("Single", "Double", "Deluxe", "Suite")


def _sql_enum(values: tuple[str, ...]) -> str:
    """Render constants as a SQL ENUM value list, e.g. 'A', 'B'."""
    return ", ".join(f"'{value}'" for value in values)


# ENUMs + CHECKs mean the database itself rejects an unknown room type or
# status and a non-positive room number or amount; every new booking
# starts out as Booked
CREATE_BOOKING_TABLE_SQL = f"""
    CREATE TABLE IF NOT EXISTS booking (
        id            INT AUTO_INCREMENT PRIMARY KEY,
        customer_name VARCHAR(100)   NOT NULL,
        room_number   INT            NOT NULL CHECK (room_number > 0),
        room_type     ENUM({_sql_enum(ROOM_TYPES)}) NOT NULL,
        amount        DECIMAL(10, 2) NOT NULL CHECK (amount > 0),
        status        ENUM({_sql_enum(BOOKING_STATUSES)})
                      NOT NULL DEFAULT '{STATUS_BOOKED}'
    )
"""

BOOKING_COLUMNS = "id, customer_name, room_number, room_type, amount, status"

MIN_ROOM_NUMBER = 1
MIN_AMOUNT = 0.0


# ---------- Input helpers ----------

def read_text(prompt: str) -> str:
    """Prompt until the user enters a non-empty string."""
    while True:
        value = input(prompt).strip()
        if value:
            return value
        print("Value cannot be empty. Try again.")


def read_int(prompt: str, minimum: int | None = None) -> int:
    """Prompt until the user enters a whole number (optionally >= minimum)."""
    while True:
        try:
            value = int(input(prompt))
        except ValueError:
            print("Please enter a whole number.")
            continue
        if minimum is not None and value < minimum:
            print(f"Value must be at least {minimum}.")
            continue
        return value


def read_amount(prompt: str) -> float:
    """Prompt until the user enters an amount greater than zero."""
    while True:
        try:
            value = float(input(prompt))
        except ValueError:
            print("Please enter a number.")
            continue
        if value <= MIN_AMOUNT:
            print(f"Amount must be greater than {MIN_AMOUNT:g}.")
            continue
        return value


def read_room_type(prompt: str) -> str:
    """Show the numbered room types and prompt until one is picked."""
    for number, room_type in enumerate(ROOM_TYPES, start=1):
        print(f"  {number}. {room_type}")
    while True:
        choice = read_int(prompt, minimum=1)
        if choice <= len(ROOM_TYPES):
            return ROOM_TYPES[choice - 1]
        print(f"Choose a number from 1 to {len(ROOM_TYPES)}.")


# ---------- Database actions ----------

def create_booking_table(cursor, connection) -> None:
    """Create the booking table on first run; a no-op if it already exists."""
    cursor.execute(CREATE_BOOKING_TABLE_SQL)
    connection.commit()


def print_bookings(bookings: list[tuple]) -> None:
    """Print booking rows as an aligned table."""
    print(
        f"{'ID':<5}{'Customer':<25}{'Room':>6}  {'Type':<10}"
        f"{'Amount':>12}  {'Status':<12}"
    )
    print("-" * 72)
    for booking_id, customer_name, room_number, room_type, amount, status in bookings:
        print(
            f"{booking_id:<5}{customer_name:<25}{room_number:>6}  {room_type:<10}"
            f"{amount:>12.2f}  {status:<12}"
        )


def get_booking_status(cursor, booking_id: int) -> str | None:
    """Return the booking's current status, or None if the ID doesn't exist."""
    cursor.execute("SELECT status FROM booking WHERE id = %s", (booking_id,))
    row = cursor.fetchone()
    return row[0] if row else None


def change_status(cursor, connection, booking_id: int, from_status: str, to_status: str) -> bool:
    """Move a booking from one status to another; return True if it moved.

    The expected current status lives in the WHERE clause so check-and-change
    is one atomic statement — two sessions can't both apply the same transition.
    """
    cursor.execute(
        "UPDATE booking SET status = %s WHERE id = %s AND status = %s",
        (to_status, booking_id, from_status),
    )
    connection.commit()
    return cursor.rowcount > 0


def create_booking(cursor, connection) -> None:
    """Prompt for booking details and insert a new row (status defaults to Booked)."""
    customer_name = read_text("Enter Customer Name: ")
    room_number = read_int("Enter Room Number: ", minimum=MIN_ROOM_NUMBER)
    room_type = read_room_type("Select Room Type: ")
    amount = read_amount("Enter Amount: ")

    # Parameterized query — values are bound, never concatenated,
    # so user input can't inject SQL
    cursor.execute(
        "INSERT INTO booking (customer_name, room_number, room_type, amount) "
        "VALUES (%s, %s, %s, %s)",
        (customer_name, room_number, room_type, amount),
    )
    connection.commit()

    print(f"Booking Created Successfully! (ID: {cursor.lastrowid})")


def display_bookings(cursor) -> None:
    """Fetch and print every booking, ordered by ID."""
    cursor.execute(f"SELECT {BOOKING_COLUMNS} FROM booking ORDER BY id")
    bookings = cursor.fetchall()

    if bookings:
        print_bookings(bookings)
    else:
        print("No Bookings Found!")


def get_booking_by_id(cursor) -> None:
    """Look up a single booking by ID and print it."""
    booking_id = read_int("Enter Booking ID: ")

    cursor.execute(
        f"SELECT {BOOKING_COLUMNS} FROM booking WHERE id = %s",
        (booking_id,),
    )
    booking = cursor.fetchone()

    if booking:
        print_bookings([booking])
    else:
        print("Booking Not Found!")


def update_room(cursor, connection) -> None:
    """Change the room number, the room type, or both on an active booking.

    Only Booked or Checked In bookings can move rooms; finished or
    cancelled bookings are kept as a historical record.
    """
    booking_id = read_int("Enter Booking ID: ")

    print("1. Update Room Number")
    print("2. Update Room Type")
    print("3. Update Both")
    choice = read_int("Enter Choice: ")

    if choice == 1:
        assignments = "room_number = %s"
        values = (read_int("Enter New Room Number: ", minimum=MIN_ROOM_NUMBER),)
    elif choice == 2:
        assignments = "room_type = %s"
        values = (read_room_type("Select New Room Type: "),)
    elif choice == 3:
        assignments = "room_number = %s, room_type = %s"
        values = (
            read_int("Enter New Room Number: ", minimum=MIN_ROOM_NUMBER),
            read_room_type("Select New Room Type: "),
        )
    else:
        print("Invalid Choice!")
        return

    cursor.execute(
        f"UPDATE booking SET {assignments} WHERE id = %s AND status IN (%s, %s)",
        (*values, booking_id, STATUS_BOOKED, STATUS_CHECKED_IN),
    )
    connection.commit()

    if cursor.rowcount > 0:
        print("Room Updated Successfully!")
        return

    # rowcount is 0 for an unknown ID, an inactive booking, or unchanged values
    status = get_booking_status(cursor, booking_id)
    if status is None:
        print("Booking Not Found!")
    elif status in (STATUS_CHECKED_OUT, STATUS_CANCELLED):
        print(f"Cannot Update Room — Booking is {status}!")
    else:
        print("No Changes Made — Room is Already Set to Those Values.")


def check_in(cursor, connection) -> None:
    """Mark a Booked booking as Checked In."""
    booking_id = read_int("Enter Booking ID: ")

    if change_status(cursor, connection, booking_id, STATUS_BOOKED, STATUS_CHECKED_IN):
        print("Checked In Successfully!")
        return

    status = get_booking_status(cursor, booking_id)
    if status is None:
        print("Booking Not Found!")
    elif status == STATUS_CHECKED_IN:
        print("Guest Already Checked In!")
    else:
        print(f"Cannot Check In — Booking is {status}!")


def check_out(cursor, connection) -> None:
    """Mark a Checked In booking as Checked Out."""
    booking_id = read_int("Enter Booking ID: ")

    if change_status(cursor, connection, booking_id, STATUS_CHECKED_IN, STATUS_CHECKED_OUT):
        print("Checked Out Successfully!")
        return

    status = get_booking_status(cursor, booking_id)
    if status is None:
        print("Booking Not Found!")
    elif status == STATUS_CHECKED_OUT:
        print("Guest Already Checked Out!")
    elif status == STATUS_BOOKED:
        print("Cannot Check Out — Guest Has Not Checked In Yet!")
    else:
        print(f"Cannot Check Out — Booking is {status}!")


def cancel_booking(cursor, connection) -> None:
    """Cancel a booking that hasn't been checked into yet; the record is kept."""
    booking_id = read_int("Enter Booking ID: ")

    if change_status(cursor, connection, booking_id, STATUS_BOOKED, STATUS_CANCELLED):
        print("Booking Cancelled Successfully!")
        return

    status = get_booking_status(cursor, booking_id)
    if status is None:
        print("Booking Not Found!")
    elif status == STATUS_CANCELLED:
        print("Booking Already Cancelled!")
    else:
        print(f"Cannot Cancel — Booking is {status}!")


def delete_all_bookings(cursor, connection) -> None:
    """Delete every booking after an explicit confirmation."""
    confirm = input("Delete ALL bookings? This cannot be undone (y/n): ")
    if confirm.strip().lower() != "y":
        print("Cancelled.")
        return

    cursor.execute("DELETE FROM booking")
    connection.commit()

    print(f"Deleted {cursor.rowcount} Booking(s).")


# ---------- Menu loop ----------

def main() -> None:
    """Run the interactive hotel-booking menu until the user exits.

    The booking table is created first if needed. The connection is
    always closed afterwards, even if an error occurs.
    """
    connection = mysql.connector.connect(**load_db_config())
    cursor = connection.cursor()

    try:
        create_booking_table(cursor, connection)

        while True:
            print("\n====== HOTEL ROOM BOOKING ======")
            print("1. Create Booking")
            print("2. Display All Bookings")
            print("3. Search Booking By ID")
            print("4. Update Room")
            print("5. Check In")
            print("6. Check Out")
            print("7. Cancel Booking")
            print("8. Delete All Bookings")
            print("9. Exit")
            choice = read_int("Enter Choice: ")

            try:
                if choice == 1:
                    create_booking(cursor, connection)
                elif choice == 2:
                    display_bookings(cursor)
                elif choice == 3:
                    get_booking_by_id(cursor)
                elif choice == 4:
                    update_room(cursor, connection)
                elif choice == 5:
                    check_in(cursor, connection)
                elif choice == 6:
                    check_out(cursor, connection)
                elif choice == 7:
                    cancel_booking(cursor, connection)
                elif choice == 8:
                    delete_all_bookings(cursor, connection)
                elif choice == 9:
                    print("Thank You!")
                    break
                else:
                    print("Invalid Choice!")
            except mysql.connector.Error as error:
                # Undo the failed statement, report it, and keep the menu running
                connection.rollback()
                print("Database Error:", error)
    finally:
        # Always release DB resources, even if the loop exits via an error
        cursor.close()
        connection.close()


if __name__ == "__main__":
    main()
