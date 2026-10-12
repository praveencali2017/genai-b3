"""Menu-driven Restaurant Order Management System backed by MySQL.

Connects using settings from the project `.env` (via `db_config`),
creates the `restaurant_order` table if it does not exist yet, and lets
the user create, display, look up, update quantity or bill, cancel, and
delete orders through a numbered command loop.
"""

import mysql.connector

from db_config import load_db_config

STATUS_PLACED = "Placed"
STATUS_CANCELLED = "Cancelled"
ORDER_STATUSES = (STATUS_PLACED, STATUS_CANCELLED)


def _sql_enum(values: tuple[str, ...]) -> str:
    """Render constants as a SQL ENUM value list, e.g. 'A', 'B'."""
    return ", ".join(f"'{value}'" for value in values)


# `order` is a reserved word in SQL, hence `restaurant_order`. The ENUM and
# CHECKs mean the database itself rejects an unknown status and a
# non-positive quantity or bill; every new order starts out as Placed
CREATE_ORDER_TABLE_SQL = f"""
    CREATE TABLE IF NOT EXISTS restaurant_order (
        id            INT AUTO_INCREMENT PRIMARY KEY,
        customer_name VARCHAR(100)   NOT NULL,
        item_name     VARCHAR(100)   NOT NULL,
        quantity      INT            NOT NULL CHECK (quantity > 0),
        bill_amount   DECIMAL(10, 2) NOT NULL CHECK (bill_amount > 0),
        status        ENUM({_sql_enum(ORDER_STATUSES)})
                      NOT NULL DEFAULT '{STATUS_PLACED}'
    )
"""

ORDER_COLUMNS = "id, customer_name, item_name, quantity, bill_amount, status"

MIN_QUANTITY = 1
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


# ---------- Database actions ----------

def create_order_table(cursor, connection) -> None:
    """Create the order table on first run; a no-op if it already exists."""
    cursor.execute(CREATE_ORDER_TABLE_SQL)
    connection.commit()


def print_orders(orders: list[tuple]) -> None:
    """Print order rows as an aligned table."""
    print(
        f"{'ID':<5}{'Customer':<22}{'Item':<22}{'Qty':>5}"
        f"{'Bill':>12}  {'Status':<10}"
    )
    print("-" * 78)
    for order_id, customer_name, item_name, quantity, bill_amount, status in orders:
        print(
            f"{order_id:<5}{customer_name:<22}{item_name:<22}{quantity:>5}"
            f"{bill_amount:>12.2f}  {status:<10}"
        )


def get_order_status(cursor, order_id: int) -> str | None:
    """Return the order's current status, or None if the ID doesn't exist."""
    cursor.execute("SELECT status FROM restaurant_order WHERE id = %s", (order_id,))
    row = cursor.fetchone()
    return row[0] if row else None


def change_status(cursor, connection, order_id: int, from_status: str, to_status: str) -> bool:
    """Move an order from one status to another; return True if it moved.

    The expected current status lives in the WHERE clause so check-and-change
    is one atomic statement — two sessions can't both apply the same transition.
    """
    cursor.execute(
        "UPDATE restaurant_order SET status = %s WHERE id = %s AND status = %s",
        (to_status, order_id, from_status),
    )
    connection.commit()
    return cursor.rowcount > 0


def update_placed_order(cursor, connection, order_id: int, column: str, value) -> None:
    """Set one column on a Placed order and report the outcome.

    Cancelled orders are kept unchanged as a historical record. `column` is
    always a fixed name chosen by the caller, never user input.
    """
    cursor.execute(
        f"UPDATE restaurant_order SET {column} = %s WHERE id = %s AND status = %s",
        (value, order_id, STATUS_PLACED),
    )
    connection.commit()

    if cursor.rowcount > 0:
        print("Order Updated Successfully!")
        return

    # rowcount is 0 for an unknown ID, a cancelled order, or an unchanged value
    status = get_order_status(cursor, order_id)
    if status is None:
        print("Order Not Found!")
    elif status == STATUS_CANCELLED:
        print(f"Cannot Update — Order is {status}!")
    else:
        print("No Changes Made — Order Already Has That Value.")


def create_order(cursor, connection) -> None:
    """Prompt for order details and insert a new row (status defaults to Placed)."""
    customer_name = read_text("Enter Customer Name: ")
    item_name = read_text("Enter Item Name: ")
    quantity = read_int("Enter Quantity: ", minimum=MIN_QUANTITY)
    bill_amount = read_amount("Enter Bill Amount: ")

    # Parameterized query — values are bound, never concatenated,
    # so user input can't inject SQL
    cursor.execute(
        "INSERT INTO restaurant_order (customer_name, item_name, quantity, bill_amount) "
        "VALUES (%s, %s, %s, %s)",
        (customer_name, item_name, quantity, bill_amount),
    )
    connection.commit()

    print(f"Order Created Successfully! (ID: {cursor.lastrowid})")


def display_orders(cursor) -> None:
    """Fetch and print every order, ordered by ID."""
    cursor.execute(f"SELECT {ORDER_COLUMNS} FROM restaurant_order ORDER BY id")
    orders = cursor.fetchall()

    if orders:
        print_orders(orders)
    else:
        print("No Orders Found!")


def get_order_by_id(cursor) -> None:
    """Look up a single order by ID and print it."""
    order_id = read_int("Enter Order ID: ")

    cursor.execute(
        f"SELECT {ORDER_COLUMNS} FROM restaurant_order WHERE id = %s",
        (order_id,),
    )
    order = cursor.fetchone()

    if order:
        print_orders([order])
    else:
        print("Order Not Found!")


def update_quantity(cursor, connection) -> None:
    """Change the quantity on a Placed order (the bill is updated separately)."""
    order_id = read_int("Enter Order ID: ")
    quantity = read_int("Enter New Quantity: ", minimum=MIN_QUANTITY)
    update_placed_order(cursor, connection, order_id, "quantity", quantity)


def update_bill(cursor, connection) -> None:
    """Change the bill amount on a Placed order."""
    order_id = read_int("Enter Order ID: ")
    bill_amount = read_amount("Enter New Bill Amount: ")
    update_placed_order(cursor, connection, order_id, "bill_amount", bill_amount)


def cancel_order(cursor, connection) -> None:
    """Cancel a Placed order; the record is kept."""
    order_id = read_int("Enter Order ID: ")

    if change_status(cursor, connection, order_id, STATUS_PLACED, STATUS_CANCELLED):
        print("Order Cancelled Successfully!")
        return

    status = get_order_status(cursor, order_id)
    if status is None:
        print("Order Not Found!")
    else:
        print("Order Already Cancelled!")


def delete_all_orders(cursor, connection) -> None:
    """Delete every order after an explicit confirmation."""
    confirm = input("Delete ALL orders? This cannot be undone (y/n): ")
    if confirm.strip().lower() != "y":
        print("Cancelled.")
        return

    cursor.execute("DELETE FROM restaurant_order")
    connection.commit()

    print(f"Deleted {cursor.rowcount} Order(s).")


# ---------- Menu loop ----------

def main() -> None:
    """Run the interactive restaurant-order menu until the user exits.

    The order table is created first if needed. The connection is
    always closed afterwards, even if an error occurs.
    """
    connection = mysql.connector.connect(**load_db_config())
    cursor = connection.cursor()

    try:
        create_order_table(cursor, connection)

        while True:
            print("\n====== RESTAURANT ORDER MANAGEMENT ======")
            print("1. Create Order")
            print("2. Display All Orders")
            print("3. Search Order By ID")
            print("4. Update Quantity")
            print("5. Update Bill")
            print("6. Cancel Order")
            print("7. Delete All Orders")
            print("8. Exit")
            choice = read_int("Enter Choice: ")

            try:
                if choice == 1:
                    create_order(cursor, connection)
                elif choice == 2:
                    display_orders(cursor)
                elif choice == 3:
                    get_order_by_id(cursor)
                elif choice == 4:
                    update_quantity(cursor, connection)
                elif choice == 5:
                    update_bill(cursor, connection)
                elif choice == 6:
                    cancel_order(cursor, connection)
                elif choice == 7:
                    delete_all_orders(cursor, connection)
                elif choice == 8:
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
