"""Menu-driven Product Inventory Management System backed by MySQL.

Connects using settings from the project `.env` (via `db_config`),
creates the `product` table if it does not exist yet, and lets the user
add, display, search, re-price, restock, sell down, and delete products
through a numbered command loop.
"""

import mysql.connector

from db_config import load_db_config

# CHECK constraints mean the database itself rejects negative stock or a
# non-positive price, even if a bad value slips past input validation
CREATE_PRODUCT_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS product (
        id       INT AUTO_INCREMENT PRIMARY KEY,
        name     VARCHAR(100)   NOT NULL,
        quantity INT            NOT NULL DEFAULT 0 CHECK (quantity >= 0),
        price    DECIMAL(10, 2) NOT NULL CHECK (price > 0),
        category VARCHAR(50)    NOT NULL
    )
"""

SELECT_PRODUCT_COLUMNS = "SELECT id, name, category, quantity, price FROM product"

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

def create_product_table(cursor, connection) -> None:
    """Create the product table on first run; a no-op if it already exists."""
    cursor.execute(CREATE_PRODUCT_TABLE_SQL)
    connection.commit()


def print_products(products: list[tuple]) -> None:
    """Print product rows as an aligned table."""
    print(f"{'ID':<5}{'Name':<25}{'Category':<15}{'Qty':>8}{'Price':>12}")
    print("-" * 65)
    for product_id, name, category, quantity, price in products:
        print(f"{product_id:<5}{name:<25}{category:<15}{quantity:>8}{price:>12}")


def product_exists(cursor, product_id: int) -> bool:
    """Return True if a product with this ID is in the table."""
    cursor.execute("SELECT 1 FROM product WHERE id = %s", (product_id,))
    return cursor.fetchone() is not None


def add_product(cursor, connection) -> None:
    """Prompt for product details and insert a new row."""
    name = read_text("Enter Name: ")
    category = read_text("Enter Category: ")
    quantity = read_int("Enter Quantity: ", minimum=0)
    price = read_price("Enter Price: ")

    # Parameterized query — values are bound, never concatenated,
    # so user input can't inject SQL
    cursor.execute(
        "INSERT INTO product (name, quantity, price, category) "
        "VALUES (%s, %s, %s, %s)",
        (name, quantity, price, category),
    )
    connection.commit()

    print(f"Product Added Successfully! (ID: {cursor.lastrowid})")


def display_products(cursor) -> None:
    """Fetch and print every product, ordered by ID."""
    cursor.execute(f"{SELECT_PRODUCT_COLUMNS} ORDER BY id")
    products = cursor.fetchall()

    if products:
        print_products(products)
    else:
        print("No Products Found!")


def search_product(cursor) -> None:
    """Find products by exact ID or by partial, case-insensitive name."""
    print("1. Search By ID")
    print("2. Search By Name")
    choice = read_int("Enter Choice: ")

    if choice == 1:
        cursor.execute(
            f"{SELECT_PRODUCT_COLUMNS} WHERE id = %s",
            (read_int("Enter Product ID: "),),
        )
    elif choice == 2:
        cursor.execute(
            f"{SELECT_PRODUCT_COLUMNS} WHERE name LIKE %s ORDER BY id",
            (f"%{read_text('Enter Name (or part of it): ')}%",),
        )
    else:
        print("Invalid Choice!")
        return

    products = cursor.fetchall()
    if products:
        print_products(products)
    else:
        print("Product Not Found!")


def update_price(cursor, connection) -> None:
    """Set a new price for a product."""
    product_id = read_int("Enter Product ID: ")
    new_price = read_price("Enter New Price: ")

    cursor.execute(
        "UPDATE product SET price = %s WHERE id = %s",
        (new_price, product_id),
    )
    connection.commit()

    # rowcount is 0 both when the ID is missing and when the price is unchanged
    if cursor.rowcount > 0:
        print("Price Updated Successfully!")
    elif product_exists(cursor, product_id):
        print("Price Unchanged (same as current price).")
    else:
        print("Product Not Found!")


def add_stock(cursor, connection) -> None:
    """Increase a product's quantity by the entered amount."""
    product_id = read_int("Enter Product ID: ")
    amount = read_int("Enter Quantity To Add: ", minimum=1)

    # Relative update in SQL (not read-then-write in Python) so concurrent
    # sessions can't overwrite each other's stock changes
    cursor.execute(
        "UPDATE product SET quantity = quantity + %s WHERE id = %s",
        (amount, product_id),
    )
    connection.commit()

    if cursor.rowcount > 0:
        print("Stock Added Successfully!")
    else:
        print("Product Not Found!")


def reduce_stock(cursor, connection) -> None:
    """Decrease a product's quantity, refusing to go below zero."""
    product_id = read_int("Enter Product ID: ")
    amount = read_int("Enter Quantity To Remove: ", minimum=1)

    # The availability check lives in the WHERE clause so check-and-change is
    # one atomic statement — two sessions can't both take the last units
    cursor.execute(
        "UPDATE product SET quantity = quantity - %s "
        "WHERE id = %s AND quantity >= %s",
        (amount, product_id, amount),
    )
    connection.commit()

    if cursor.rowcount > 0:
        print("Stock Reduced Successfully!")
    elif product_exists(cursor, product_id):
        print("Insufficient Stock!")
    else:
        print("Product Not Found!")


def delete_product(cursor, connection) -> None:
    """Delete a product row by ID."""
    product_id = read_int("Enter Product ID: ")

    cursor.execute("DELETE FROM product WHERE id = %s", (product_id,))
    connection.commit()

    if cursor.rowcount > 0:
        print("Product Deleted Successfully!")
    else:
        print("Product Not Found!")


def delete_all_products(cursor, connection) -> None:
    """Delete every product after an explicit confirmation."""
    confirm = input("Delete ALL products? This cannot be undone (y/n): ")
    if confirm.strip().lower() != "y":
        print("Cancelled.")
        return

    cursor.execute("DELETE FROM product")
    connection.commit()

    print(f"Deleted {cursor.rowcount} Product(s).")


# ---------- Menu loop ----------

def main() -> None:
    """Run the interactive product-inventory menu until the user exits.

    The product table is created first if needed. The connection is
    always closed afterwards, even if an error occurs.
    """
    connection = mysql.connector.connect(**load_db_config())
    cursor = connection.cursor()

    try:
        create_product_table(cursor, connection)

        while True:
            print("\n====== PRODUCT INVENTORY MANAGEMENT ======")
            print("1. Add Product")
            print("2. Display All Products")
            print("3. Search Product")
            print("4. Update Price")
            print("5. Add Stock")
            print("6. Reduce Stock")
            print("7. Delete Product")
            print("8. Delete All Products")
            print("9. Exit")
            choice = read_int("Enter Choice: ")

            try:
                if choice == 1:
                    add_product(cursor, connection)
                elif choice == 2:
                    display_products(cursor)
                elif choice == 3:
                    search_product(cursor)
                elif choice == 4:
                    update_price(cursor, connection)
                elif choice == 5:
                    add_stock(cursor, connection)
                elif choice == 6:
                    reduce_stock(cursor, connection)
                elif choice == 7:
                    delete_product(cursor, connection)
                elif choice == 8:
                    delete_all_products(cursor, connection)
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
