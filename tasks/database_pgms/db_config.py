"""Load MySQL connection settings from the project's .env file."""

import os
from pathlib import Path

from dotenv import load_dotenv

ENV_FILENAME = ".env"
MODULE_DIR = Path(__file__).resolve().parent
REQUIRED_VARS = ("DB_HOST", "DB_USER", "DB_PASSWORD", "DB_NAME")


def find_env_file(start: Path = MODULE_DIR) -> Path | None:
    """Return the nearest .env, checking `start` and then each parent folder.

    The search starts at this module's folder, not the working directory,
    so the same file is found no matter where the program is run from.
    """
    for folder in (start, *start.parents):
        candidate = folder / ENV_FILENAME
        if candidate.is_file():
            return candidate
    return None


def load_db_config() -> dict[str, str]:
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
        "user": os.environ["DB_USER"],
        "password": os.environ["DB_PASSWORD"],
        "database": os.environ["DB_NAME"],
    }
