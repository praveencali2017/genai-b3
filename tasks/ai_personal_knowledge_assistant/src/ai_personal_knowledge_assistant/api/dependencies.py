"""FastAPI dependency providers.

Every provider reads from the `AppContainer` the lifespan stored on
`app.state`, so nothing is built per request and tests can replace any of them
with `app.dependency_overrides`.
"""

from collections.abc import Iterator
from typing import Annotated, cast

from fastapi import Depends, Request

from ai_personal_knowledge_assistant.api.container import AppContainer
from ai_personal_knowledge_assistant.ingestion.pipeline import IngestionPipeline
from ai_personal_knowledge_assistant.storage.database import session_scope
from ai_personal_knowledge_assistant.storage.document_repository import (
    DocumentRepository,
)
from ai_personal_knowledge_assistant.vectorstore.chroma_store import ChromaVectorStore


def get_container(request: Request) -> AppContainer:
    return cast(AppContainer, request.app.state.container)


Container = Annotated[AppContainer, Depends(get_container)]


def get_ingestion_pipeline(container: Container) -> IngestionPipeline:
    return container.ingestion_pipeline


def get_vector_store(container: Container) -> ChromaVectorStore:
    return container.vector_store


def get_document_repository(container: Container) -> Iterator[DocumentRepository]:
    """A repository in one transaction, committed when the request succeeds."""
    with session_scope(container.session_factory) as session:
        yield DocumentRepository(session)
