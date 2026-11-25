"""Request and response bodies of the HTTP API."""

from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, Field, PositiveInt, StringConstraints

from ai_personal_knowledge_assistant.rag.answerer import Answer, Source
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


QUESTION_MAX_LENGTH = 2000

Question = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=1, max_length=QUESTION_MAX_LENGTH
    ),
]


class AskRequest(BaseModel):
    question: Question = Field(examples=["How many casual leaves are allowed?"])
    session_id: UUID | None = Field(
        default=None,
        description="Continue this conversation; omit to start a new one. "
        "The response returns the id to send next time.",
    )
    document_ids: list[PositiveInt] | None = Field(
        default=None,
        description="Search only these documents; omit to search all of them.",
    )


class SourceOut(BaseModel):
    citation: int = Field(description="The [n] marker the answer uses for it.")
    document_id: int
    filename: str
    page: int | None = Field(description="Page in a PDF; null for DOCX and TXT.")
    chunk_index: int
    score: float = Field(description="Relevance to the question, 0..1.")
    snippet: str = Field(description="The start of the excerpt.")

    @classmethod
    def from_source(cls, source: Source) -> Self:
        return cls(
            citation=source.citation,
            document_id=source.document_id,
            filename=source.filename,
            page=source.page,
            chunk_index=source.chunk_index,
            score=source.score,
            snippet=source.snippet,
        )


class AskResponse(BaseModel):
    answer: str
    is_grounded: bool = Field(
        description="False when the documents don't answer the question."
    )
    sources: list[SourceOut]
    session_id: UUID

    @classmethod
    def from_answer(cls, answer: Answer, session_id: UUID) -> Self:
        return cls(
            answer=answer.text,
            is_grounded=answer.is_grounded,
            sources=[SourceOut.from_source(source) for source in answer.sources],
            session_id=session_id,
        )
