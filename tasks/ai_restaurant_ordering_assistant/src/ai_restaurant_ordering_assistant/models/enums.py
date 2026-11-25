"""The fixed vocabularies stored in status and role columns."""

from enum import StrEnum


class Role(StrEnum):
    USER = "USER"
    ADMIN = "ADMIN"


class OrderStatus(StrEnum):
    CONFIRMED = "CONFIRMED"
    PREPARING = "PREPARING"
    OUT_FOR_DELIVERY = "OUT_FOR_DELIVERY"
    DELIVERED = "DELIVERED"
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
    CREATE_ORDER = "CREATE_ORDER"
    CANCEL_ORDER = "CANCEL_ORDER"


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
