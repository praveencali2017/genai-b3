"""Document endpoints: upload a file, list the stored documents."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, UploadFile, status

from ai_personal_knowledge_assistant.api.dependencies import (
    get_document_repository,
    get_ingestion_pipeline,
)
from ai_personal_knowledge_assistant.api.schemas import DocumentOut, ErrorResponse
from ai_personal_knowledge_assistant.core.errors import FileTooLargeError
from ai_personal_knowledge_assistant.ingestion.pipeline import IngestionPipeline
from ai_personal_knowledge_assistant.storage.document_repository import (
    DocumentRepository,
)
from ai_personal_knowledge_assistant.storage.records import DocumentStatus

router = APIRouter(prefix="/documents", tags=["documents"])


def _error(description: str) -> dict[str, Any]:
    return {"model": ErrorResponse, "description": description}


@router.post(
    "/upload",
    status_code=status.HTTP_201_CREATED,
    summary="Upload and index a PDF, DOCX or TXT file",
    responses={
        status.HTTP_409_CONFLICT: _error("The same file was already uploaded."),
        status.HTTP_413_CONTENT_TOO_LARGE: _error("The file exceeds MAX_UPLOAD_MB."),
        status.HTTP_415_UNSUPPORTED_MEDIA_TYPE: _error(
            "Not a PDF, DOCX or TXT file, or the content doesn't match the extension."
        ),
        status.HTTP_422_UNPROCESSABLE_CONTENT: _error(
            "The file is empty, has no text, or is damaged."
        ),
        status.HTTP_503_SERVICE_UNAVAILABLE: _error("The vector store failed."),
    },
)
def upload_document(
    file: UploadFile,
    pipeline: Annotated[IngestionPipeline, Depends(get_ingestion_pipeline)],
) -> DocumentOut:
    """Extract the file's text, split it into chunks, embed them into the vector
    store and record the document. Synchronous: it returns once the document is
    `ready` to be searched."""
    # Read one byte past the limit: enough to tell "too large" without holding
    # an arbitrarily large body in memory.
    content = file.file.read(pipeline.max_bytes + 1)
    if len(content) > pipeline.max_bytes:
        raise FileTooLargeError(file.size or len(content), pipeline.max_bytes)
    record = pipeline.ingest(file.filename or "", content)
    return DocumentOut.from_record(record)


@router.get("", summary="List uploaded documents, newest first")
def list_documents(
    repository: Annotated[DocumentRepository, Depends(get_document_repository)],
    status_filter: Annotated[
        DocumentStatus,
        Query(alias="status", description="Only documents in this status."),
    ] = DocumentStatus.READY,
) -> list[DocumentOut]:
    return [
        DocumentOut.from_record(record)
        for record in repository.list_documents(status_filter)
    ]
