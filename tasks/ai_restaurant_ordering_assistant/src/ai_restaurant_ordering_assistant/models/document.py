from datetime import datetime

from sqlalchemy import CHAR, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ai_restaurant_ordering_assistant.models.base import (
    Base,
    Timestamp,
    enum_column,
    utc_now,
)
from ai_restaurant_ordering_assistant.models.enums import DocumentStatus


class Document(Base):
    """An uploaded policy document. Uploading a file with the same name again
    adds a new `version` and supersedes the old one; only ACTIVE versions are
    searched."""

    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("filename", "version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    filename: Mapped[str] = mapped_column(String(255))
    sha256: Mapped[str] = mapped_column(CHAR(64), unique=True)
    version: Mapped[int]
    status: Mapped[DocumentStatus] = mapped_column(enum_column(DocumentStatus))
    chunk_count: Mapped[int] = mapped_column(default=0)
    uploaded_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(Timestamp, default=utc_now)
