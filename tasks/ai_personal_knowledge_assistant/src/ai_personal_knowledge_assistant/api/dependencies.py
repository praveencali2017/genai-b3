"""FastAPI dependency providers.

Every provider reads from the `AppContainer` the lifespan stored on
`app.state`, so nothing is built per request and tests can replace any of them
with `app.dependency_overrides`.
"""

from collections.abc import Iterator
from typing import Annotated, cast

from fastapi import Depends, Request
from sqlalchemy.orm import Session, sessionmaker

from ai_personal_knowledge_assistant.agent.runner import SingleAgent
from ai_personal_knowledge_assistant.api.container import AppContainer
from ai_personal_knowledge_assistant.ingestion.pipeline import IngestionPipeline
from ai_personal_knowledge_assistant.rag.answerer import GroundedAnswerer
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


def get_answerer(container: Container) -> GroundedAnswerer:
    return container.answerer


def get_agent(container: Container) -> SingleAgent:
    return container.agent


def get_session_factory(container: Container) -> sessionmaker[Session]:
    """For routes that need more than one short transaction per request.

    The chat routes read history, wait seconds for the model, then write. A
    request-long transaction (like `get_document_repository`'s) would hold the
    database open across that wait, and on SQLite block uploads from
    committing; separate transactions before and after the call avoid that.
    """
    return container.session_factory


SessionFactory = Annotated[sessionmaker[Session], Depends(get_session_factory)]
