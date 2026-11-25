"""Composition root: builds the long-lived services the API shares.

The app's lifespan opens one `AppContainer` and keeps it on `app.state` for the
life of the process; route dependencies read from it (see `dependencies.py`).
Tests open their own container on temporary folders with a fake embedder.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from sqlalchemy.orm import Session, sessionmaker

from ai_personal_knowledge_assistant.core.settings import Settings
from ai_personal_knowledge_assistant.ingestion.pipeline import (
    IngestionConfig,
    IngestionPipeline,
)
from ai_personal_knowledge_assistant.rag.retriever import DocumentRetriever
from ai_personal_knowledge_assistant.storage.database import (
    create_db_engine,
    create_session_factory,
    init_db,
)
from ai_personal_knowledge_assistant.vectorstore.chroma_store import ChromaVectorStore
from ai_personal_knowledge_assistant.vectorstore.embedder import (
    Embedder,
    SentenceTransformerEmbedder,
)


@dataclass(frozen=True, slots=True)
class AppContainer:
    settings: Settings
    session_factory: sessionmaker[Session]
    vector_store: ChromaVectorStore
    retriever: DocumentRetriever
    ingestion_pipeline: IngestionPipeline


@contextmanager
def open_container(
    settings: Settings, embedder: Embedder | None = None
) -> Iterator[AppContainer]:
    """Create the database tables, open Chroma, and wire the services.

    `embedder` defaults to the configured Sentence Transformers model, which is
    only loaded when something is first embedded. The database engine is
    disposed when the block exits.
    """
    engine = create_db_engine(settings.database_url)
    try:
        init_db(engine)
        session_factory = create_session_factory(engine)
        vector_store = ChromaVectorStore.persistent(
            settings.chroma_dir,
            settings.chroma_collection,
            embedder or SentenceTransformerEmbedder(settings.embedding_model),
        )
        yield AppContainer(
            settings=settings,
            session_factory=session_factory,
            vector_store=vector_store,
            retriever=DocumentRetriever(
                vector_store, settings.top_k, settings.min_relevance
            ),
            ingestion_pipeline=build_ingestion_pipeline(
                settings, session_factory, vector_store
            ),
        )
    finally:
        engine.dispose()


def build_ingestion_pipeline(
    settings: Settings,
    session_factory: sessionmaker[Session],
    vector_store: ChromaVectorStore,
) -> IngestionPipeline:
    return IngestionPipeline(
        session_factory,
        vector_store,
        IngestionConfig(
            upload_dir=settings.upload_dir,
            max_bytes=settings.max_upload_bytes,
            chunk_size=settings.chunk_size,
            chunk_overlap=settings.chunk_overlap,
        ),
    )
