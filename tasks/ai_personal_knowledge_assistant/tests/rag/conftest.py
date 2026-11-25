"""Fixtures for answering tests: real ingestion into on-disk Chroma, fake model."""

from collections.abc import Iterator
from pathlib import Path
from typing import cast

import openai
import pytest
from sqlalchemy.orm import Session, sessionmaker

from ai_personal_knowledge_assistant.ingestion.pipeline import (
    IngestionConfig,
    IngestionPipeline,
)
from ai_personal_knowledge_assistant.rag.answerer import GroundedAnswerer
from ai_personal_knowledge_assistant.rag.retriever import DocumentRetriever
from ai_personal_knowledge_assistant.storage.database import (
    create_db_engine,
    create_session_factory,
    init_db,
)
from ai_personal_knowledge_assistant.storage.records import DocumentRecord
from ai_personal_knowledge_assistant.vectorstore.chroma_store import ChromaVectorStore
from tests.fakes import FakeOpenAI

SAMPLE_DOCS_DIR = Path(__file__).resolve().parents[2] / "sample_docs"
TEST_MODEL = "test-model"
# FakeEmbedder scores are word overlap, not MiniLM similarity: "How many casual
# leaves are allowed?" shares only "casual" with the leave chunk (score ~0.11),
# while an unrelated question scores 0. This threshold keeps that contrast.
FAKE_EMBEDDER_MIN_RELEVANCE = 0.05


@pytest.fixture
def session_factory() -> Iterator[sessionmaker[Session]]:
    engine = create_db_engine("sqlite://")
    init_db(engine)
    yield create_session_factory(engine)
    engine.dispose()


@pytest.fixture
def hr_policy(
    tmp_path: Path,
    session_factory: sessionmaker[Session],
    vector_store: ChromaVectorStore,
) -> DocumentRecord:
    """sample_docs/HR_Policy.pdf, ingested through the real pipeline."""
    pipeline = IngestionPipeline(
        session_factory,
        vector_store,
        IngestionConfig(
            upload_dir=tmp_path / "uploads",
            max_bytes=1_000_000,
            chunk_size=800,
            chunk_overlap=150,
        ),
    )
    content = (SAMPLE_DOCS_DIR / "HR_Policy.pdf").read_bytes()
    return pipeline.ingest("HR_Policy.pdf", content)


@pytest.fixture
def fake_openai() -> FakeOpenAI:
    return FakeOpenAI()


def make_answerer(
    store: ChromaVectorStore,
    client: FakeOpenAI | None,
    *,
    top_k: int = 5,
    min_relevance: float = FAKE_EMBEDDER_MIN_RELEVANCE,
) -> GroundedAnswerer:
    return GroundedAnswerer(
        DocumentRetriever(store, top_k=top_k, min_relevance=min_relevance),
        cast(openai.OpenAI | None, client),
        TEST_MODEL,
    )
