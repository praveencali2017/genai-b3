"""Load MySQL connection settings from the nearest .env file."""

import os
from pathlib import Path

from dotenv import load_dotenv

ENV_FILENAME = ".env"
MODULE_DIR = Path(__file__).resolve().parent
REQUIRED_VARS = ("DB_HOST", "DB_USER", "DB_PASSWORD", "DB_NAME")
# DB_PORT is optional; MySQL's standard port is used when it isn't set.
DEFAULT_PORT = 3306
MIN_PORT = 1
MAX_PORT = 65535


def find_env_file(start: Path = MODULE_DIR) -> Path | None:
    """Return the nearest .env, checking `start` and then each parent folder.

    The search starts at this module's folder, not the working directory,
    so the same file is found no matter where the server is started from.
    """
    for folder in (start, *start.parents):
        candidate = folder / ENV_FILENAME
        if candidate.is_file():
            return candidate
    return None


def read_port() -> int:
    """Return DB_PORT as an int, or MySQL's default port when it isn't set.

    Hosted MySQL (e.g. Aiven) often listens on a non-default port, so an
    unparseable value must fail at startup, not as a connection timeout.
    """
    raw = os.getenv("DB_PORT", "").strip()
    if not raw:
        return DEFAULT_PORT
    invalid = RuntimeError(
        f"DB_PORT must be a whole number from {MIN_PORT} to {MAX_PORT}, got {raw!r}."
    )
    try:
        port = int(raw)
    except ValueError:
        raise invalid from None
    if not MIN_PORT <= port <= MAX_PORT:
        raise invalid
    return port


def load_db_config() -> dict[str, str | int]:
    """Return connection kwargs for mysql.connector.connect.

    Raises RuntimeError naming every missing variable, so a bad .env
    fails at startup instead of as a confusing connection error.
    Variables already set in the environment take precedence over .env.
    """
    env_file = find_env_file()
    if env_file is not None:
        load_dotenv(env_file)

    missing = [name for name in REQUIRED_VARS if not os.getenv(name)]
    if missing:
        source = env_file or f"no {ENV_FILENAME} found in this folder or any parent"
        raise RuntimeError(
            f"Missing database settings ({source}): {', '.join(missing)}. "
            "Copy .env.example to .env and fill in the values."
        )

    return {
        "host": os.environ["DB_HOST"],
        "port": read_port(),
        "user": os.environ["DB_USER"],
        "password": os.environ["DB_PASSWORD"],
        "database": os.environ["DB_NAME"],
    }
