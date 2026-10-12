"""Menu-driven Library Management System backed by MySQL.

Connects using settings from the project `.env` (via `db_config`),
creates the `book` table if it does not exist yet, and lets the user
add, display, look up, issue, return, update, and delete books through a
numbered command loop.
"""

import mysql.connector

from db_config import load_db_config

STATUS_AVAILABLE = "Available"
STATUS_ISSUED = "Issued"

# ENUM + DEFAULT means the database itself rejects any other status,
# and every new book starts out on the shelf
CREATE_BOOK_TABLE_SQL = f"""
    CREATE TABLE IF NOT EXISTS book (
        id     INT AUTO_INCREMENT PRIMARY KEY,
        title  VARCHAR(200) NOT NULL,
        author VARCHAR(100) NOT NULL,
        price  DECIMAL(10, 2) NOT NULL,
        status ENUM('{STATUS_AVAILABLE}', '{STATUS_ISSUED}')
               NOT NULL DEFAULT '{STATUS_AVAILABLE}'
    )
"""

MIN_PRICE = 0.0


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


def read_price(prompt: str) -> float:
    """Prompt until the user enters a price greater than zero."""
    while True:
        try:
            value = float(input(prompt))
        except ValueError:
            print("Please enter a number.")
            continue
        if value <= MIN_PRICE:
            print(f"Price must be greater than {MIN_PRICE:g}.")
            continue
        return value


# ---------- Database actions ----------

def create_book_table(cursor, connection) -> None:
    """Create the book table on first run; a no-op if it already exists."""
    cursor.execute(CREATE_BOOK_TABLE_SQL)
    connection.commit()


def print_books(books: list[tuple]) -> None:
    """Print book rows as an aligned table."""
    print(f"{'ID':<5}{'Title':<30}{'Author':<20}{'Price':>10}  {'Status':<10}")
    print("-" * 77)
    for book_id, title, author, price, status in books:
        print(f"{book_id:<5}{title:<30}{author:<20}{price:>10}  {status:<10}")


def book_exists(cursor, book_id: int) -> bool:
    """Return True if a book with this ID is in the table."""
    cursor.execute("SELECT 1 FROM book WHERE id = %s", (book_id,))
    return cursor.fetchone() is not None


def add_book(cursor, connection) -> None:
    """Prompt for book details and insert a new row (status defaults to Available)."""
    title = read_text("Enter Title: ")
    author = read_text("Enter Author: ")
    price = read_price("Enter Price: ")

    # Parameterized query — values are bound, never concatenated,
    # so user input can't inject SQL
    cursor.execute(
        "INSERT INTO book (title, author, price) VALUES (%s, %s, %s)",
        (title, author, price),
    )
    connection.commit()

    print(f"Book Added Successfully! (ID: {cursor.lastrowid})")


def display_books(cursor) -> None:
    """Fetch and print every book, ordered by ID."""
    cursor.execute("SELECT id, title, author, price, status FROM book ORDER BY id")
    books = cursor.fetchall()

    if books:
        print_books(books)
    else:
        print("No Books Found!")


def get_book_by_id(cursor) -> None:
    """Look up a single book by ID and print it."""
    book_id = read_int("Enter Book ID: ")

    cursor.execute(
        "SELECT id, title, author, price, status FROM book WHERE id = %s",
        (book_id,),
    )
    book = cursor.fetchone()

    if book:
        print_books([book])
    else:
        print("Book Not Found!")


def issue_book(cursor, connection) -> None:
    """Mark an available book as issued."""
    book_id = read_int("Enter Book ID: ")

    # The status check lives in the WHERE clause so check-and-change is one
    # atomic statement — two sessions can't both issue the same copy
    cursor.execute(
        "UPDATE book SET status = %s WHERE id = %s AND status = %s",
        (STATUS_ISSUED, book_id, STATUS_AVAILABLE),
    )
    connection.commit()

    if cursor.rowcount > 0:
        print("Book Issued Successfully!")
    elif book_exists(cursor, book_id):
        print("Book Already Issued!")
    else:
        print("Book Not Found!")


def return_book(cursor, connection) -> None:
    """Mark an issued book as available again."""
    book_id = read_int("Enter Book ID: ")

    cursor.execute(
        "UPDATE book SET status = %s WHERE id = %s AND status = %s",
        (STATUS_AVAILABLE, book_id, STATUS_ISSUED),
    )
    connection.commit()

    if cursor.rowcount > 0:
        print("Book Returned Successfully!")
    elif book_exists(cursor, book_id):
        print("Book Was Not Issued!")
    else:
        print("Book Not Found!")


def update_book(cursor, connection) -> None:
    """Update the title, author, price, or all three for a book.

    Status is deliberately not editable here — it only changes through
    issue/return, so the Available <-> Issued transition stays controlled.
    """
    book_id = read_int("Enter Book ID: ")

    print("1. Update Title")
    print("2. Update Author")
    print("3. Update Price")
    print("4. Update All")
    choice = read_int("Enter Choice: ")

    if choice == 1:
        sql = "UPDATE book SET title = %s WHERE id = %s"
        values = (read_text("Enter New Title: "), book_id)
    elif choice == 2:
        sql = "UPDATE book SET author = %s WHERE id = %s"
        values = (read_text("Enter New Author: "), book_id)
    elif choice == 3:
        sql = "UPDATE book SET price = %s WHERE id = %s"
        values = (read_price("Enter New Price: "), book_id)
    elif choice == 4:
        sql = "UPDATE book SET title = %s, author = %s, price = %s WHERE id = %s"
        values = (
            read_text("Enter New Title: "),
            read_text("Enter New Author: "),
            read_price("Enter New Price: "),
            book_id,
        )
    else:
        print("Invalid Choice!")
        return

    cursor.execute(sql, values)
    connection.commit()

    # rowcount is 0 when no row matched the ID (or the values were unchanged)
    if cursor.rowcount > 0:
        print("Book Updated Successfully!")
    else:
        print("Book Not Found or No Changes Made!")


def delete_book(cursor, connection) -> None:
    """Delete a book row by ID."""
    book_id = read_int("Enter Book ID: ")

    cursor.execute("DELETE FROM book WHERE id = %s", (book_id,))
    connection.commit()

    if cursor.rowcount > 0:
        print("Book Deleted Successfully!")
    else:
        print("Book Not Found!")


def delete_all_books(cursor, connection) -> None:
    """Delete every book after an explicit confirmation."""
    confirm = input("Delete ALL books? This cannot be undone (y/n): ")
    if confirm.strip().lower() != "y":
        print("Cancelled.")
        return

    cursor.execute("DELETE FROM book")
    connection.commit()

    print(f"Deleted {cursor.rowcount} Book(s).")


# ---------- Menu loop ----------

def main() -> None:
    """Run the interactive library-management menu until the user exits.

    The book table is created first if needed. The connection is
    always closed afterwards, even if an error occurs.
    """
    connection = mysql.connector.connect(**load_db_config())
    cursor = connection.cursor()

    try:
        create_book_table(cursor, connection)

        while True:
            print("\n====== LIBRARY MANAGEMENT ======")
            print("1. Add Book")
            print("2. Display All Books")
            print("3. Search Book By ID")
            print("4. Issue Book")
            print("5. Return Book")
            print("6. Update Book Details")
            print("7. Delete Book")
            print("8. Delete All Books")
            print("9. Exit")
            choice = read_int("Enter Choice: ")

            try:
                if choice == 1:
                    add_book(cursor, connection)
                elif choice == 2:
                    display_books(cursor)
                elif choice == 3:
                    get_book_by_id(cursor)
                elif choice == 4:
                    issue_book(cursor, connection)
                elif choice == 5:
                    return_book(cursor, connection)
                elif choice == 6:
                    update_book(cursor, connection)
                elif choice == 7:
                    delete_book(cursor, connection)
                elif choice == 8:
                    delete_all_books(cursor, connection)
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
