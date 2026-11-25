"""Shared test fixtures."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from chromadb.api.client import SharedSystemClient
from chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 import ONNXMiniLM_L6_V2
from sqlalchemy.orm import Session, sessionmaker

from ai_personal_knowledge_assistant.core.settings import Settings, get_settings
from ai_personal_knowledge_assistant.ingestion.pipeline import (
    IngestionConfig,
    IngestionPipeline,
)
from ai_personal_knowledge_assistant.storage.database import (
    create_db_engine,
    create_session_factory,
    init_db,
)
from ai_personal_knowledge_assistant.storage.records import DocumentRecord
from ai_personal_knowledge_assistant.vectorstore.chroma_store import ChromaVectorStore
from tests.fakes import FakeEmbedder, FakeOpenAI

SAMPLE_DOCS_DIR = Path(__file__).resolve().parents[1] / "sample_docs"
SETTING_NAMES = [name.upper() for name in Settings.model_fields]
TEST_COLLECTION = "test-chunks"


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Never read the developer's shell variables or real `.env` file."""
    for name in SETTING_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def no_chroma_default_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail loudly if Chroma ever reaches for its own ONNX embedding model.

    We always pass our own embeddings; Chroma's default would download ~80 MB.
    """

    def refuse_download(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("Chroma tried to download its default embedding model")

    monkeypatch.setattr(
        ONNXMiniLM_L6_V2, "_download_model_if_not_exists", refuse_download
    )


def release_chroma() -> None:
    """Close Chroma's cached per-path clients, as a process exit would.

    Chroma shares one in-process system per data folder; clearing the cache
    releases its file handles, so the next client on that folder starts cold.
    """
    SharedSystemClient.clear_system_cache()


@pytest.fixture
def release_chroma_clients() -> Iterator[None]:
    """Release Chroma's clients after the test, so state can't leak between tests."""
    yield
    release_chroma()


@pytest.fixture
def chroma_dir(tmp_path: Path, release_chroma_clients: None) -> Path:
    return tmp_path / "chroma"


@pytest.fixture
def fake_embedder() -> FakeEmbedder:
    return FakeEmbedder()


@pytest.fixture
def vector_store(chroma_dir: Path, fake_embedder: FakeEmbedder) -> ChromaVectorStore:
    """An empty on-disk store in this test's own temporary folder."""
    return ChromaVectorStore.persistent(chroma_dir, TEST_COLLECTION, fake_embedder)


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
