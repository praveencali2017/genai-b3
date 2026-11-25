"""Storage vocabulary shared with the upper layers: enums and immutable records.

Repositories return these records instead of ORM objects, so callers never hold
an instance bound to (or detached from) a `Session`, can't trigger lazy loads
after the session closed, and can't change a row by mutating an attribute.
The API layer converts them into its response schemas.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

# One JSON object as stored in the `sources` / `tool_calls` columns. The records
# are frozen, but these nested dicts are plain JSON data and aren't deep-frozen.
JsonObject = dict[str, Any]


# Longest stored filename; MySQL's VARCHAR needs an explicit bound.
FILENAME_MAX_LENGTH = 255


class FileType(StrEnum):
    PDF = "pdf"
    DOCX = "docx"
    TXT = "txt"


class DocumentStatus(StrEnum):
    """Ingestion lifecycle: rows start `processing`, then end `ready` or `failed`."""

    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


@dataclass(frozen=True, slots=True)
class DocumentRecord:
    id: int
    filename: str
    file_type: FileType
    size_bytes: int
    sha256: str
    page_count: int | None
    chunk_count: int
    status: DocumentStatus
    error_message: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ChatSessionRecord:
    id: UUID
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ChatMessageRecord:
    id: int
    session_id: UUID
    role: MessageRole
    content: str
    sources: list[JsonObject] | None
    tool_calls: list[JsonObject] | None
    created_at: datetime
