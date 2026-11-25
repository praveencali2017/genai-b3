"""Request and response bodies of the HTTP API."""

from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, Field

from ai_personal_knowledge_assistant.storage.records import (
    DocumentRecord,
    DocumentStatus,
    FileType,
)


class ErrorResponse(BaseModel):
    """The body of every error the API returns on purpose."""

    detail: str = Field(examples=["This file was already uploaded as 'a.pdf'."])


class DocumentOut(BaseModel):
    id: int
    filename: str = Field(description="Sanitized name of the uploaded file.")
    file_type: FileType
    size_bytes: int
    page_count: int | None = Field(
        description="Pages in a PDF (up to the last page with text); "
        "null for DOCX and TXT, which have no fixed pages."
    )
    chunk_count: int = Field(description="Chunks stored in the vector database.")
    status: DocumentStatus
    created_at: datetime

    @classmethod
    def from_record(cls, record: DocumentRecord) -> Self:
        return cls(
            id=record.id,
            filename=record.filename,
            file_type=record.file_type,
            size_bytes=record.size_bytes,
            page_count=record.page_count,
            chunk_count=record.chunk_count,
            status=record.status,
            created_at=record.created_at,
        )


class HealthOut(BaseModel):
    status: Literal["ok"] = "ok"
    ready_documents: int
    stored_chunks: int
