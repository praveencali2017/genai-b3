"""Liveness check that also proves the database and vector store answer."""

from typing import Annotated

from fastapi import APIRouter, Depends

from ai_personal_knowledge_assistant.api.dependencies import (
    get_document_repository,
    get_vector_store,
)
from ai_personal_knowledge_assistant.api.schemas import ErrorResponse, HealthOut
from ai_personal_knowledge_assistant.storage.document_repository import (
    DocumentRepository,
)
from ai_personal_knowledge_assistant.storage.records import DocumentStatus
from ai_personal_knowledge_assistant.vectorstore.chroma_store import ChromaVectorStore

router = APIRouter(tags=["health"])


@router.get(
    "/health",
    summary="Service health and stored content counts",
    responses={503: {"model": ErrorResponse, "description": "A store is down."}},
)
def health(
    repository: Annotated[DocumentRepository, Depends(get_document_repository)],
    vector_store: Annotated[ChromaVectorStore, Depends(get_vector_store)],
) -> HealthOut:
    return HealthOut(
        ready_documents=len(repository.list_documents(DocumentStatus.READY)),
        stored_chunks=vector_store.count(),
    )
