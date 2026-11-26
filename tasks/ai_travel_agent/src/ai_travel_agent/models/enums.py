"""The fixed vocabularies stored in status and role columns."""

from enum import StrEnum


class Role(StrEnum):
    USER = "USER"
    ADMIN = "ADMIN"


class BookingStatus(StrEnum):
    CONFIRMED = "CONFIRMED"
    # The trip has taken place; set by an admin.
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class ChatMode(StrEnum):
    """Which paths produced an assistant reply."""

    GENERAL = "GENERAL"
    RAG = "RAG"
    AGENT = "AGENT"
    MIXED = "MIXED"


class ToolRunStatus(StrEnum):
    SUCCESS = "SUCCESS"
    ERROR = "ERROR"


class PendingActionType(StrEnum):
    CREATE_BOOKING = "CREATE_BOOKING"
    CANCEL_BOOKING = "CANCEL_BOOKING"


class PendingActionStatus(StrEnum):
    PENDING = "PENDING"
    # Claimed by a confirmation; the business call is in flight or was
    # interrupted, and a retry reconciles it with the same idempotency key.
    PROCESSING = "PROCESSING"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    FAILED = "FAILED"


class DocumentStatus(StrEnum):
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    FAILED = "FAILED"
