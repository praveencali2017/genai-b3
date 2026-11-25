"""Composition root: builds the long-lived services the API shares.

The app's lifespan opens one `AppContainer` and keeps it on `app.state` for the
life of the process; route dependencies read from it (see
`controllers/dependencies.py`). The language-model client is optional: without
one the shop works and only the chatbot and document upload answer 503.
"""

import secrets
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from functools import partial

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from ai_restaurant_ordering_assistant.agents.api_client import RestaurantApiClient
from ai_restaurant_ordering_assistant.agents.single_agent import SingleAgent
from ai_restaurant_ordering_assistant.config.database import (
    create_db_engine,
    create_session_factory,
)
from ai_restaurant_ordering_assistant.config.settings import Settings
from ai_restaurant_ordering_assistant.llm.openai_client import (
    build_chat_client,
    build_embedding_client,
)
from ai_restaurant_ordering_assistant.rag.embeddings import Embedder
from ai_restaurant_ordering_assistant.rag.retriever import PolicyRetriever
from ai_restaurant_ordering_assistant.rag.vector_store import ChromaVectorStore
from ai_restaurant_ordering_assistant.services.chat_service import (
    ChatConfig,
    ChatService,
)
from ai_restaurant_ordering_assistant.services.document_service import IndexingConfig
from ai_restaurant_ordering_assistant.services.rate_limiter import (
    SlidingWindowRateLimiter,
)


@dataclass(frozen=True, slots=True)
class AppContainer:
    settings: Settings
    engine: Engine
    session_factory: sessionmaker[Session]
    vector_store: ChromaVectorStore
    embedder: Embedder
    indexing: IndexingConfig
    chat_service: ChatService
    # Builds the agent's API client for one acting user id.
    api_client_factory: Callable[[int], RestaurantApiClient] = field(repr=False)
    # Authenticates the agent's calls to this API (see security/basic_auth.py).
    # New on every start, so it is never stored anywhere.
    internal_token: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class DocumentServices:
    """What indexing needs; shared by the API and the restaurant-index CLI."""

    vector_store: ChromaVectorStore
    embedder: Embedder
    indexing: IndexingConfig


def build_document_services(settings: Settings) -> DocumentServices:
    return DocumentServices(
        vector_store=ChromaVectorStore(
            settings.chroma_path, settings.chroma_collection
        ),
        embedder=Embedder(
            build_embedding_client(settings), settings.openai_embedding_model
        ),
        indexing=IndexingConfig(
            upload_dir=settings.upload_dir,
            max_bytes=settings.max_upload_mb * 1024 * 1024,
            chunk_size=settings.chunk_size,
            chunk_overlap=settings.chunk_overlap,
        ),
    )


@contextmanager
def open_container(settings: Settings) -> Iterator[AppContainer]:
    """Wire the services; the database engine is disposed when the block exits."""
    engine = create_db_engine(settings.database_url.get_secret_value())
    try:
        session_factory = create_session_factory(engine)
        documents = build_document_services(settings)
        internal_token = secrets.token_urlsafe(32)
        api_client_factory = partial(
            _api_client, settings.internal_api_url, internal_token, settings
        )
        chat_client = build_chat_client(settings)
        agent = (
            SingleAgent(
                chat_client, settings.openai_model, settings.agent_max_iterations
            )
            if chat_client is not None
            else None
        )
        retriever = PolicyRetriever(
            session_factory,
            documents.vector_store,
            documents.embedder,
            settings.top_k,
            settings.min_relevance,
        )
        chat_service = ChatService(
            session_factory=session_factory,
            agent=agent,
            retriever=retriever,
            api_client_factory=api_client_factory,
            rate_limiter=SlidingWindowRateLimiter(settings.chat_rate_limit_per_minute),
            config=ChatConfig(
                context_turns=settings.chat_context_turns,
                pending_action_ttl=timedelta(
                    minutes=settings.pending_action_ttl_minutes
                ),
            ),
        )
        yield AppContainer(
            settings=settings,
            engine=engine,
            session_factory=session_factory,
            vector_store=documents.vector_store,
            embedder=documents.embedder,
            indexing=documents.indexing,
            chat_service=chat_service,
            api_client_factory=api_client_factory,
            internal_token=internal_token,
        )
    finally:
        engine.dispose()


def _api_client(
    base_url: str, internal_token: str, settings: Settings, acting_user_id: int
) -> RestaurantApiClient:
    return RestaurantApiClient(
        base_url, internal_token, acting_user_id, settings.tool_timeout_seconds
    )
