"""Password hashing with Argon2id (argon2-cffi's defaults)."""

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_hasher = PasswordHasher()

# Verified against when the username doesn't exist, so a login for an unknown
# user takes as long as one with a wrong password and doesn't reveal which
# usernames exist.
_DUMMY_HASH = _hasher.hash("not-a-real-password")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    """True if `password` matches; a None hash (unknown user) is never a match
    but costs the same time to check."""
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and (
            password_hash is not None
        )
    except (VerificationError, InvalidHashError):
        return False
