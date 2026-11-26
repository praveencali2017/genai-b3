"""ORM tables for document metadata and chat history.

Only `storage` imports this module; everything above it works with the records
in `storage.records`. Column types are chosen to be portable between SQLite and
MySQL: bounded `String` lengths (MySQL needs a VARCHAR length), enums stored as
VARCHAR + CHECK rather than native ENUMs, the generic `JSON` and `Uuid` types,
and a UTC timestamp type that round-trips timezone-aware datetimes on both.
"""

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    Dialect,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    TypeDecorator,
    Uuid,
)
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeEngine

from ai_personal_knowledge_assistant.storage.records import (
    FILENAME_MAX_LENGTH,
    DocumentStatus,
    FileType,
    JsonObject,
    MessageRole,
)

SHA256_HEX_LENGTH = 64


class UtcDateTime(TypeDecorator[datetime]):
    """A timestamp stored as naive UTC and always read back as aware UTC.

    Neither SQLite nor MySQL's DATETIME keeps a timezone, so a plain
    `DateTime(timezone=True)` comes back naive. Normalising at this boundary
    means the rest of the app only ever sees aware UTC datetimes. On MySQL it
    keeps microseconds (DATETIME(6)), so rows created in the same second still
    sort correctly.
    """

    impl = DateTime
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> TypeEngine[Any]:
        if dialect.name == "mysql":
            return dialect.type_descriptor(mysql.DATETIME(fsp=6))
        return dialect.type_descriptor(DateTime())

    def process_bind_param(
        self, value: datetime | None, dialect: Dialect
    ) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError(f"naive datetime {value!r}; pass a timezone-aware one")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(
        self, value: datetime | None, dialect: Dialect
    ) -> datetime | None:
        return None if value is None else value.replace(tzinfo=UTC)


def utc_now() -> datetime:
    return datetime.now(UTC)


def _string_enum(enum_class: type[StrEnum], constraint_name: str) -> Enum:
    """VARCHAR + CHECK holding the enum's lowercase *values* (not member names),
    so the stored data is readable and identical on every backend."""
    return Enum(
        enum_class,
        name=constraint_name,
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        length=max(len(member.value) for member in enum_class),
        values_callable=lambda members: [member.value for member in members],
    )


class Base(DeclarativeBase):
    type_annotation_map = {datetime: UtcDateTime()}


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint("size_bytes >= 0", name="ck_documents_size_bytes"),
        CheckConstraint("chunk_count >= 0", name="ck_documents_chunk_count"),
        CheckConstraint(
            "page_count IS NULL OR page_count >= 0", name="ck_documents_page_count"
        ),
    )

    # An integer key: short, readable ids in URLs and Chroma vector ids
    # ("{id}:{chunk_index}"), and nothing client-facing needs to be unguessable.
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    filename: Mapped[str] = mapped_column(String(FILENAME_MAX_LENGTH))
    file_type: Mapped[FileType] = mapped_column(
        _string_enum(FileType, "ck_documents_file_type")
    )
    size_bytes: Mapped[int]
    sha256: Mapped[str] = mapped_column(
        String(SHA256_HEX_LENGTH), unique=True, index=True
    )
    page_count: Mapped[int | None]
    chunk_count: Mapped[int] = mapped_column(default=0)
    status: Mapped[DocumentStatus] = mapped_column(
        _string_enum(DocumentStatus, "ck_documents_status"),
        default=DocumentStatus.PROCESSING,
    )
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utc_now, index=True)


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    # A UUID because the client generates/holds it as `session_id`; stored as
    # CHAR(32) on SQLite/MySQL by the generic `Uuid` type.
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(default=utc_now)

    messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    # Autoincrement ids double as the message order within a session: they are
    # strictly increasing, unlike timestamps, which can tie.
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[MessageRole] = mapped_column(
        _string_enum(MessageRole, "ck_chat_messages_role")
    )
    content: Mapped[str] = mapped_column(Text)
    sources: Mapped[list[JsonObject] | None] = mapped_column(JSON(none_as_null=True))
    tool_calls: Mapped[list[JsonObject] | None] = mapped_column(JSON(none_as_null=True))
    created_at: Mapped[datetime] = mapped_column(default=utc_now)

    session: Mapped[ChatSession] = relationship(back_populates="messages")
