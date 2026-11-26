"""Policy document endpoints (admin only): upload and index, list versions."""

from typing import Annotated

from fastapi import APIRouter, File, UploadFile, status

from ai_travel_agent.container import AppContainer
from ai_travel_agent.controllers.dependencies import (
    Container,
    DbSession,
)
from ai_travel_agent.controllers.responses import (
    ADMIN_ERRORS,
    CONFLICT,
)
from ai_travel_agent.schemas.document import DocumentResponse
from ai_travel_agent.schemas.error import ErrorResponse
from ai_travel_agent.security.basic_auth import AdminUser
from ai_travel_agent.services.document_service import DocumentService

router = APIRouter(prefix="/api/documents", tags=["documents"], responses=ADMIN_ERRORS)


def _service(session: DbSession, container: AppContainer) -> DocumentService:
    return DocumentService(
        session, container.vector_store, container.embedder, container.indexing
    )


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    responses={
        **CONFLICT,
        413: {"model": ErrorResponse, "description": "File too large"},
        415: {"model": ErrorResponse, "description": "Not a PDF, DOCX or TXT"},
        422: {"model": ErrorResponse, "description": "No extractable text"},
        503: {"model": ErrorResponse, "description": "Embedding service down"},
    },
)
def upload_document(
    file: Annotated[UploadFile, File(description="PDF, DOCX or TXT policy document")],
    session: DbSession,
    container: Container,
    admin: AdminUser,
) -> DocumentResponse:
    """Index a document. Uploading a file with the same name again creates a
    new version and retires the old one from search."""
    # Read one byte past the limit: enough to know it's too big, never more.
    data = file.file.read(container.indexing.max_bytes + 1)
    document = _service(session, container).upload(file.filename or "", data, admin.id)
    return DocumentResponse.model_validate(document)


@router.get("")
def list_documents(
    session: DbSession, container: Container, _admin: AdminUser
) -> list[DocumentResponse]:
    """Every version of every document, newest version first per filename."""
    documents = _service(session, container).list_documents()
    return [DocumentResponse.model_validate(d) for d in documents]
