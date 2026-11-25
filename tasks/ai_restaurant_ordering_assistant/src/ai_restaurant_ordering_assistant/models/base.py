"""Declarative base and the column types every table shares."""

from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import JSON, DateTime, Enum, MetaData, Text
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import DeclarativeBase

# Deterministic constraint names, so Alembic migrations can refer to them.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

# MySQL DATETIME drops fractions of a second unless asked; chat messages sent in
# the same second must still sort by time.
Timestamp = DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql")
LongText = Text().with_variant(mysql.LONGTEXT(), "mysql")
Json = JSON().with_variant(mysql.JSON(), "mysql")


def utc_now() -> datetime:
    """The current time in UTC, naive, as stored in DATETIME columns."""
    return datetime.now(UTC).replace(tzinfo=None)


def enum_column[E: StrEnum](enum_class: type[E]) -> Enum:
    """A VARCHAR column holding the enum's values.

    VARCHAR rather than MySQL's native ENUM, so adding a status later is a code
    change, not an ALTER TABLE.
    """
    return Enum(
        enum_class,
        native_enum=False,
        create_constraint=False,
        length=max(len(member.value) for member in enum_class),
        values_callable=lambda members: [member.value for member in members],
        validate_strings=True,
    )


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
