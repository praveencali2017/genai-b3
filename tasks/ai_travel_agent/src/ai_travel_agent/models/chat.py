from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ai_travel_agent.models.base import (
    Base,
    Json,
    LongText,
    Timestamp,
    enum_column,
    utc_now,
)
from ai_travel_agent.models.enums import (
    MessageRole,
    PendingActionStatus,
    PendingActionType,
    ToolRunStatus,
)


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(Timestamp, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        Timestamp, default=utc_now, onupdate=utc_now
    )


class ChatMessage(Base):
    """One turn. `seq_no` numbers a session's messages 1, 2, 3...; history is
    read back in that order.

    `message_metadata` (column `metadata`, a name SQLAlchemy reserves) holds
    the reply's mode, citations, pending action id and error outcome.
    """

    __tablename__ = "chat_messages"
    __table_args__ = (UniqueConstraint("session_id", "seq_no"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE")
    )
    seq_no: Mapped[int]
    role: Mapped[MessageRole] = mapped_column(enum_column(MessageRole))
    content: Mapped[str] = mapped_column(LongText)
    message_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", Json, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(Timestamp, default=utc_now)

    tool_runs: Mapped[list["ToolRun"]] = relationship(
        cascade="all, delete-orphan", order_by="ToolRun.id", lazy="selectin"
    )


class ToolRun(Base):
    """An audit record of one tool call the agent made for a reply."""

    __tablename__ = "tool_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[int] = mapped_column(
        ForeignKey("chat_messages.id", ondelete="CASCADE"), index=True
    )
    tool_name: Mapped[str] = mapped_column(String(64))
    args: Mapped[Any] = mapped_column(Json)
    result: Mapped[Any] = mapped_column(Json)
    status: Mapped[ToolRunStatus] = mapped_column(enum_column(ToolRunStatus))
    created_at: Mapped[datetime] = mapped_column(Timestamp, default=utc_now)


class PendingAction(Base):
    """A booking or cancellation the agent proposed, waiting for the user to
    confirm or reject it.

    `payload` holds the exact terms shown to the user; `result` the outcome of
    executing them. `idempotency_key` is sent with the business call, so a
    retried confirmation can never create a second booking.
    """

    __tablename__ = "pending_actions"
    __table_args__ = (Index(None, "session_id", "status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE")
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    action: Mapped[PendingActionType] = mapped_column(enum_column(PendingActionType))
    payload: Mapped[dict[str, Any]] = mapped_column(Json)
    result: Mapped[dict[str, Any] | None] = mapped_column(Json)
    expires_at: Mapped[datetime] = mapped_column(Timestamp)
    status: Mapped[PendingActionStatus] = mapped_column(
        enum_column(PendingActionStatus), default=PendingActionStatus.PENDING
    )
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(Timestamp, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        Timestamp, default=utc_now, onupdate=utc_now
    )
