"""Menu-driven ATM Account Management System backed by MySQL.

Connects using settings from the project `.env` (via `db_config`),
creates the `account` table if it does not exist yet, and lets the user
create accounts, check balance, deposit, withdraw, change PIN, search, and
close accounts through a numbered command loop.

PINs are never stored: each account keeps a random salt and a PBKDF2 hash
of its PIN, and PINs are typed through `getpass` so they don't echo.
"""

import hashlib
import hmac
import secrets
from decimal import Decimal, InvalidOperation
from getpass import getpass

import mysql.connector

from db_config import load_db_config

STATUS_ACTIVE = "Active"
STATUS_CLOSED = "Closed"
ACCOUNT_STATUSES = (STATUS_ACTIVE, STATUS_CLOSED)

ACCOUNT_TYPES = ("Savings", "Current")

FIRST_ACCOUNT_NUMBER = 1_000_000_001
PIN_LENGTH = 4
PIN_SALT_BYTES = 16
PIN_HASH_ITERATIONS = 200_000
DUMMY_PIN_SALT = bytes(PIN_SALT_BYTES)

# Matches DECIMAL(12, 2): ten digits before the point, two after
MONEY_PLACES = Decimal("0.01")
MAX_MONEY = Decimal("9999999999.99")
ZERO = Decimal(0)


def _sql_enum(values: tuple[str, ...]) -> str:
    """Render constants as a SQL ENUM value list, e.g. 'A', 'B'."""
    return ", ".join(f"'{value}'" for value in values)


# The database hands out 10-digit account numbers, and its ENUMs + CHECK
# reject an unknown type or status and a negative balance — so even a buggy
# withdrawal can't overdraw an account. Every new account starts out Active
CREATE_ACCOUNT_TABLE_SQL = f"""
    CREATE TABLE IF NOT EXISTS account (
        account_number BIGINT AUTO_INCREMENT PRIMARY KEY,
        customer_name  VARCHAR(100)   NOT NULL,
        account_type   ENUM({_sql_enum(ACCOUNT_TYPES)}) NOT NULL,
        balance        DECIMAL(12, 2) NOT NULL DEFAULT 0 CHECK (balance >= 0),
        pin_salt       BINARY({PIN_SALT_BYTES}) NOT NULL,
        pin_hash       BINARY(32)     NOT NULL,
        status         ENUM({_sql_enum(ACCOUNT_STATUSES)})
                       NOT NULL DEFAULT '{STATUS_ACTIVE}'
    ) AUTO_INCREMENT = {FIRST_ACCOUNT_NUMBER}
"""

# Search results leave out the balance (that needs the PIN) and PIN columns
PUBLIC_COLUMNS = "account_number, customer_name, account_type, status"


# ---------- PIN hashing ----------

def hash_pin(pin: str, salt: bytes) -> bytes:
    """Return the PBKDF2-SHA256 hash of a PIN under the given salt."""
    return hashlib.pbkdf2_hmac("sha256", pin.encode(), salt, PIN_HASH_ITERATIONS)


def new_pin_credentials(pin: str) -> tuple[bytes, bytes]:
    """Return a fresh (salt, hash) pair to store for a PIN."""
    salt = secrets.token_bytes(PIN_SALT_BYTES)
    return salt, hash_pin(pin, salt)


def is_pin_correct(pin: str, salt: bytes, stored_hash: bytes) -> bool:
    """Check a PIN against its stored hash in constant time."""
    return hmac.compare_digest(hash_pin(pin, bytes(salt)), bytes(stored_hash))


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


def read_choice(prompt: str, options: tuple[str, ...]) -> str:
    """Show the numbered options and prompt until one is picked."""
    for number, option in enumerate(options, start=1):
        print(f"  {number}. {option}")
    while True:
        choice = read_int(prompt, minimum=1)
        if choice <= len(options):
            return options[choice - 1]
        print(f"Choose a number from 1 to {len(options)}.")


def parse_money(text: str) -> Decimal:
    """Parse a money amount with at most two decimal places.

    Raises ValueError with a user-facing message for anything that isn't
    a finite, non-negative amount the balance column can hold.
    """
    try:
        value = Decimal(text.strip())
    except InvalidOperation:
        raise ValueError("Please enter a number.") from None
    if not value.is_finite():
        raise ValueError("Please enter a number.")
    # Range checks come first: quantize() raises InvalidOperation on huge
    # values like 1e30, which would otherwise escape as a crash
    if value < ZERO:
        raise ValueError("Amount cannot be negative.")
    if value > MAX_MONEY:
        raise ValueError(f"Amount cannot exceed {MAX_MONEY:,}.")
    if value != value.quantize(MONEY_PLACES):
        raise ValueError("Amount can have at most 2 decimal places.")
    return value.quantize(MONEY_PLACES)


def read_amount(prompt: str) -> Decimal:
    """Prompt until the user enters a deposit/withdrawal amount above zero."""
    while True:
        try:
            value = parse_money(input(prompt))
        except ValueError as error:
            print(error)
            continue
        if value == ZERO:
            print("Amount must be greater than 0.")
            continue
        return value


def read_opening_balance(prompt: str) -> Decimal:
    """Prompt until the user enters an opening balance (zero allowed)."""
    while True:
        try:
            return parse_money(input(prompt))
        except ValueError as error:
            print(error)


def is_valid_pin(pin: str) -> bool:
    """A PIN is exactly PIN_LENGTH ASCII digits; leading zeros count."""
    return len(pin) == PIN_LENGTH and pin.isascii() and pin.isdigit()


def read_pin(prompt: str) -> str:
    """Read a PIN without echoing it to the screen."""
    return getpass(prompt).strip()


def read_new_pin(prompt: str = "Enter New 4-Digit PIN: ") -> str:
    """Prompt until the user enters a valid PIN and confirms it."""
    while True:
        pin = read_pin(prompt)
        if not is_valid_pin(pin):
            print(f"PIN must be exactly {PIN_LENGTH} digits.")
            continue
        if read_pin("Confirm PIN: ") != pin:
            print("PINs do not match. Try again.")
            continue
        return pin


# ---------- Database actions ----------

def create_account_table(cursor, connection) -> None:
    """Create the account table on first run; a no-op if it already exists."""
    cursor.execute(CREATE_ACCOUNT_TABLE_SQL)
    connection.commit()


def print_accounts(accounts: list[tuple]) -> None:
    """Print public account rows as an aligned table."""
    print(f"{'Account No':<14}{'Customer':<25}{'Type':<10}{'Status':<8}")
    print("-" * 57)
    for account_number, customer_name, account_type, status in accounts:
        print(f"{account_number:<14}{customer_name:<25}{account_type:<10}{status:<8}")


def fetch_pin_and_status(cursor, account_number: int) -> tuple | None:
    """Return (pin_salt, pin_hash, status), or None if the account doesn't exist."""
    cursor.execute(
        "SELECT pin_salt, pin_hash, status FROM account WHERE account_number = %s",
        (account_number,),
    )
    return cursor.fetchone()


def fetch_balance_and_status(cursor, account_number: int) -> tuple[Decimal, str]:
    """Return the account's current (balance, status)."""
    cursor.execute(
        "SELECT balance, status FROM account WHERE account_number = %s",
        (account_number,),
    )
    return cursor.fetchone()


def authenticate(cursor) -> int | None:
    """Prompt for account number and PIN; return the account number if valid.

    An unknown account and a wrong PIN get the same message, so the prompt
    can't be used to discover which account numbers exist.
    """
    account_number = read_int("Enter Account Number: ", minimum=1)
    pin = read_pin("Enter PIN: ")

    row = fetch_pin_and_status(cursor, account_number)
    if row is None:
        # Hash anyway so an unknown account takes as long as a wrong PIN
        hash_pin(pin, DUMMY_PIN_SALT)
        print("Invalid Account Number or PIN!")
        return None
    if not is_pin_correct(pin, row[0], row[1]):
        print("Invalid Account Number or PIN!")
        return None
    if row[2] == STATUS_CLOSED:
        print("Account is Closed!")
        return None
    return account_number


def create_account(cursor, connection) -> None:
    """Prompt for account details and insert a new Active account."""
    customer_name = read_text("Enter Customer Name: ")
    account_type = read_choice("Select Account Type: ", ACCOUNT_TYPES)
    opening_balance = read_opening_balance("Enter Opening Balance: ")
    pin_salt, pin_hash = new_pin_credentials(read_new_pin("Set 4-Digit PIN: "))

    # Parameterized query — values are bound, never concatenated,
    # so user input can't inject SQL
    cursor.execute(
        "INSERT INTO account (customer_name, account_type, balance, pin_salt, pin_hash) "
        "VALUES (%s, %s, %s, %s, %s)",
        (customer_name, account_type, opening_balance, pin_salt, pin_hash),
    )
    connection.commit()

    print(f"Account Created Successfully! Account Number: {cursor.lastrowid}")


def check_balance(cursor) -> None:
    """Show the balance of an authenticated account."""
    account_number = authenticate(cursor)
    if account_number is None:
        return

    balance, _ = fetch_balance_and_status(cursor, account_number)
    print(f"Available Balance: {balance:,.2f}")


def deposit(cursor, connection) -> None:
    """Add money to an authenticated, still-active account."""
    account_number = authenticate(cursor)
    if account_number is None:
        return
    amount = read_amount("Enter Deposit Amount: ")

    # The status check lives in the WHERE clause so a concurrent close wins
    cursor.execute(
        "UPDATE account SET balance = balance + %s "
        "WHERE account_number = %s AND status = %s",
        (amount, account_number, STATUS_ACTIVE),
    )
    connection.commit()

    if cursor.rowcount == 0:
        print("Account is Closed!")
        return
    balance, _ = fetch_balance_and_status(cursor, account_number)
    print(f"Deposited {amount:,.2f}. New Balance: {balance:,.2f}")


def withdraw(cursor, connection) -> None:
    """Take money out of an authenticated account if the balance covers it."""
    account_number = authenticate(cursor)
    if account_number is None:
        return
    amount = read_amount("Enter Withdrawal Amount: ")

    # Check-and-debit is one atomic statement — two sessions can't both
    # pass the balance check and overdraw the account
    cursor.execute(
        "UPDATE account SET balance = balance - %s "
        "WHERE account_number = %s AND status = %s AND balance >= %s",
        (amount, account_number, STATUS_ACTIVE, amount),
    )
    connection.commit()
    is_withdrawn = cursor.rowcount > 0  # read before the SELECT below resets it

    balance, status = fetch_balance_and_status(cursor, account_number)
    if is_withdrawn:
        print(f"Please Collect {amount:,.2f}. Remaining Balance: {balance:,.2f}")
    elif status == STATUS_CLOSED:
        print("Account is Closed!")
    else:
        print(f"Insufficient Balance! Available Balance: {balance:,.2f}")


def change_pin(cursor, connection) -> None:
    """Replace the PIN of an authenticated account with a new one."""
    account_number = authenticate(cursor)
    if account_number is None:
        return

    new_pin = read_new_pin()
    old_salt, old_hash, _ = fetch_pin_and_status(cursor, account_number)
    if is_pin_correct(new_pin, old_salt, old_hash):
        print("New PIN must be different from the current PIN!")
        return

    pin_salt, pin_hash = new_pin_credentials(new_pin)
    cursor.execute(
        "UPDATE account SET pin_salt = %s, pin_hash = %s "
        "WHERE account_number = %s AND status = %s",
        (pin_salt, pin_hash, account_number, STATUS_ACTIVE),
    )
    connection.commit()

    if cursor.rowcount > 0:
        print("PIN Changed Successfully!")
    else:
        print("Account is Closed!")


def _escape_like(term: str) -> str:
    """Escape LIKE wildcards so user input matches literally."""
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def search_account(cursor) -> None:
    """Find accounts by exact account number or by part of the customer name."""
    print("1. Search By Account Number")
    print("2. Search By Customer Name")
    choice = read_int("Enter Choice: ")

    if choice == 1:
        cursor.execute(
            f"SELECT {PUBLIC_COLUMNS} FROM account WHERE account_number = %s",
            (read_int("Enter Account Number: ", minimum=1),),
        )
    elif choice == 2:
        name_pattern = f"%{_escape_like(read_text('Enter Customer Name: '))}%"
        cursor.execute(
            f"SELECT {PUBLIC_COLUMNS} FROM account "
            "WHERE customer_name LIKE %s ORDER BY account_number",
            (name_pattern,),
        )
    else:
        print("Invalid Choice!")
        return

    accounts = cursor.fetchall()
    if accounts:
        print_accounts(accounts)
    else:
        print("No Accounts Found!")


def close_account(cursor, connection) -> None:
    """Close an authenticated account once its balance is zero; the record is kept."""
    account_number = authenticate(cursor)
    if account_number is None:
        return

    # Refuse early rather than after confirmation; the UPDATE below still
    # re-checks balance = 0 atomically in case a deposit lands in between
    balance, _ = fetch_balance_and_status(cursor, account_number)
    if balance > ZERO:
        print(f"Cannot Close — Withdraw Remaining Balance of {balance:,.2f} First!")
        return

    confirm = input("Close this account? This cannot be undone (y/n): ")
    if confirm.strip().lower() != "y":
        print("Cancelled.")
        return

    cursor.execute(
        "UPDATE account SET status = %s "
        "WHERE account_number = %s AND status = %s AND balance = 0",
        (STATUS_CLOSED, account_number, STATUS_ACTIVE),
    )
    connection.commit()
    is_closed = cursor.rowcount > 0  # read before the SELECT below resets it

    balance, status = fetch_balance_and_status(cursor, account_number)
    if is_closed:
        print("Account Closed Successfully!")
    elif status == STATUS_CLOSED:
        print("Account Already Closed!")
    else:
        print(f"Cannot Close — Withdraw Remaining Balance of {balance:,.2f} First!")


# ---------- Menu loop ----------

def main() -> None:
    """Run the interactive ATM menu until the user exits.

    The account table is created first if needed. The connection is
    always closed afterwards, even if an error occurs.
    """
    connection = mysql.connector.connect(**load_db_config())
    cursor = connection.cursor()

    try:
        create_account_table(cursor, connection)

        while True:
            print("\n====== ATM ACCOUNT MANAGEMENT ======")
            print("1. Create Account")
            print("2. Check Balance")
            print("3. Deposit")
            print("4. Withdraw")
            print("5. Change PIN")
            print("6. Search Account")
            print("7. Close Account")
            print("8. Exit")
            choice = read_int("Enter Choice: ")

            try:
                if choice == 1:
                    create_account(cursor, connection)
                elif choice == 2:
                    check_balance(cursor)
                elif choice == 3:
                    deposit(cursor, connection)
                elif choice == 4:
                    withdraw(cursor, connection)
                elif choice == 5:
                    change_pin(cursor, connection)
                elif choice == 6:
                    search_account(cursor)
                elif choice == 7:
                    close_account(cursor, connection)
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
