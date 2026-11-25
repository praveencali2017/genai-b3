"""Shared test fixtures."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from chromadb.api.client import SharedSystemClient
from chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 import ONNXMiniLM_L6_V2

from ai_personal_knowledge_assistant.core.settings import Settings, get_settings
from ai_personal_knowledge_assistant.vectorstore.chroma_store import ChromaVectorStore
from tests.fakes import FakeEmbedder

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


@pytest.fixture
def release_chroma_clients() -> Iterator[None]:
    """Close Chroma's cached per-path clients after the test.

    Chroma shares one in-process system per data folder; clearing the cache
    releases its file handles and stops state leaking between tests.
    """
    yield
    SharedSystemClient.clear_system_cache()


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
