"""Load MySQL connection settings from the project's .env file."""

import os
from pathlib import Path

from dotenv import load_dotenv

# Repo root: database_pgm -> 25_basic_pgms -> tasks -> <root>
ENV_FILE = Path(__file__).resolve().parents[3] / ".env"
REQUIRED_VARS = ("DB_HOST", "DB_USER", "DB_PASSWORD", "DB_NAME")


def load_db_config() -> dict[str, str]:
    """Return connection kwargs for mysql.connector.connect.

    Raises RuntimeError naming every missing variable, so a bad .env
    fails at startup instead of as a confusing connection error.
    """
    load_dotenv(ENV_FILE)

    missing = [name for name in REQUIRED_VARS if not os.getenv(name)]
    if missing:
        raise RuntimeError(
            f"Missing database settings in .env: {', '.join(missing)}. "
            "Copy .env.example to .env and fill in the values."
        )

    return {
        "host": os.environ["DB_HOST"],
        "user": os.environ["DB_USER"],
        "password": os.environ["DB_PASSWORD"],
        "database": os.environ["DB_NAME"],
    }
