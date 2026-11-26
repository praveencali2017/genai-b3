"""Initial schema: users, packages, bookings, chat history, pending actions,
documents.

Revision ID: 0001
Revises:
Create Date: 2026-11-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TIMESTAMP = sa.DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql")
MONEY = sa.Numeric(12, 2)
JSON = sa.JSON().with_variant(mysql.JSON(), "mysql")
LONGTEXT = sa.Text().with_variant(mysql.LONGTEXT(), "mysql")


def _timestamps(*, with_updated_at: bool = True) -> list[sa.Column]:
    columns = [sa.Column("created_at", TIMESTAMP, nullable=False)]
    if with_updated_at:
        columns.append(sa.Column("updated_at", TIMESTAMP, nullable=False))
    return columns


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("username", sa.String(50), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.String(5), nullable=False),
        *_timestamps(with_updated_at=False),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("username", name="uq_users_username"),
    )

    op.create_table(
        "packages",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("destination", sa.String(80), nullable=False),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("duration_days", sa.Integer(), nullable=False),
        sa.Column("price", MONEY, nullable=False),
        sa.Column("capacity", sa.Integer(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_packages"),
        sa.CheckConstraint("price > 0", name=op.f("ck_packages_price_positive")),
        sa.CheckConstraint(
            "capacity >= 0", name=op.f("ck_packages_capacity_non_negative")
        ),
        sa.CheckConstraint(
            "duration_days > 0", name=op.f("ck_packages_duration_positive")
        ),
    )
    op.create_index(
        "ix_packages_active_destination", "packages", ["active", "destination"]
    )
    op.create_index("ix_packages_start_date", "packages", ["start_date"])

    op.create_table(
        "bookings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(9), nullable=False),
        sa.Column("total", MONEY, nullable=False),
        sa.Column("refund_amount", MONEY, nullable=True),
        sa.Column("cancelled_at", TIMESTAMP, nullable=True),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_bookings"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_bookings_user_id_users"
        ),
        sa.UniqueConstraint(
            "user_id", "idempotency_key", name="uq_bookings_user_id_idempotency_key"
        ),
        sa.CheckConstraint("total >= 0", name=op.f("ck_bookings_total_non_negative")),
        sa.CheckConstraint(
            "refund_amount >= 0", name=op.f("ck_bookings_refund_non_negative")
        ),
    )
    op.create_index("ix_bookings_user_id", "bookings", ["user_id"])
    op.create_index("ix_bookings_status", "bookings", ["status"])

    op.create_table(
        "booking_lines",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("booking_id", sa.Integer(), nullable=False),
        sa.Column("package_id", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_price", MONEY, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_booking_lines"),
        sa.ForeignKeyConstraint(
            ["booking_id"],
            ["bookings.id"],
            name="fk_booking_lines_booking_id_bookings",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["package_id"],
            ["packages.id"],
            name="fk_booking_lines_package_id_packages",
        ),
        sa.CheckConstraint(
            "quantity BETWEEN 1 AND 10", name=op.f("ck_booking_lines_quantity_range")
        ),
        sa.CheckConstraint(
            "unit_price > 0", name=op.f("ck_booking_lines_unit_price_positive")
        ),
    )
    op.create_index("ix_booking_lines_booking_id", "booking_lines", ["booking_id"])
    op.create_index("ix_booking_lines_package_id", "booking_lines", ["package_id"])

    op.create_table(
        "chat_sessions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(120), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_chat_sessions"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_chat_sessions_user_id_users"
        ),
    )
    op.create_index("ix_chat_sessions_user_id", "chat_sessions", ["user_id"])

    op.create_table(
        "chat_messages",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("seq_no", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(9), nullable=False),
        sa.Column("content", LONGTEXT, nullable=False),
        sa.Column("metadata", JSON, nullable=False),
        *_timestamps(with_updated_at=False),
        sa.PrimaryKeyConstraint("id", name="pk_chat_messages"),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["chat_sessions.id"],
            name="fk_chat_messages_session_id_chat_sessions",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "session_id", "seq_no", name="uq_chat_messages_session_id_seq_no"
        ),
    )

    op.create_table(
        "tool_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=False),
        sa.Column("tool_name", sa.String(64), nullable=False),
        sa.Column("args", JSON, nullable=False),
        sa.Column("result", JSON, nullable=False),
        sa.Column("status", sa.String(7), nullable=False),
        *_timestamps(with_updated_at=False),
        sa.PrimaryKeyConstraint("id", name="pk_tool_runs"),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["chat_messages.id"],
            name="fk_tool_runs_message_id_chat_messages",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_tool_runs_message_id", "tool_runs", ["message_id"])

    op.create_table(
        "pending_actions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(14), nullable=False),
        sa.Column("payload", JSON, nullable=False),
        sa.Column("result", JSON, nullable=True),
        sa.Column("expires_at", TIMESTAMP, nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_pending_actions"),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["chat_sessions.id"],
            name="fk_pending_actions_session_id_chat_sessions",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_pending_actions_user_id_users"
        ),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_pending_actions_idempotency_key"
        ),
    )
    op.create_index("ix_pending_actions_user_id", "pending_actions", ["user_id"])
    op.create_index(
        "ix_pending_actions_session_id_status",
        "pending_actions",
        ["session_id", "status"],
    )

    op.create_table(
        "documents",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("sha256", sa.CHAR(64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("uploaded_by", sa.Integer(), nullable=False),
        *_timestamps(with_updated_at=False),
        sa.PrimaryKeyConstraint("id", name="pk_documents"),
        sa.ForeignKeyConstraint(
            ["uploaded_by"], ["users.id"], name="fk_documents_uploaded_by_users"
        ),
        sa.UniqueConstraint(
            "filename", "version", name="uq_documents_filename_version"
        ),
        sa.UniqueConstraint("sha256", name="uq_documents_sha256"),
    )


def downgrade() -> None:
    for table in (
        "documents",
        "pending_actions",
        "tool_runs",
        "chat_messages",
        "chat_sessions",
        "booking_lines",
        "bookings",
        "packages",
        "users",
    ):
        op.drop_table(table)
