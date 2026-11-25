"""The real MiniLM model end to end. Slow: downloads ~90 MB on first run.

Run with `uv run pytest -m slow -s` to see the observed scores.
"""

from pathlib import Path

import pytest

from ai_personal_knowledge_assistant.core.settings import Settings
from ai_personal_knowledge_assistant.ingestion.chunker import chunk_pages
from ai_personal_knowledge_assistant.ingestion.extractors import extract_pages
from ai_personal_knowledge_assistant.rag.retriever import DocumentRetriever
from ai_personal_knowledge_assistant.storage.records import FileType
from ai_personal_knowledge_assistant.vectorstore.chroma_store import ChromaVectorStore
from ai_personal_knowledge_assistant.vectorstore.embedder import (
    SentenceTransformerEmbedder,
)
from tests.conftest import TEST_COLLECTION

pytestmark = pytest.mark.slow

SAMPLE_DOCS = Path(__file__).resolve().parents[2] / "sample_docs"
# The shipped defaults (no env vars, no .env) are what this test calibrates.
DEFAULTS = Settings.model_construct()
SAMPLE_FILES = [
    (1, "HR_Policy.pdf", FileType.PDF),
    (2, "Employee_Handbook.docx", FileType.DOCX),
    (3, "Travel_Policy.txt", FileType.TXT),
]


@pytest.fixture(scope="module")
def embedder() -> SentenceTransformerEmbedder:
    return SentenceTransformerEmbedder(DEFAULTS.embedding_model)


@pytest.fixture
def sample_store(
    chroma_dir: Path, embedder: SentenceTransformerEmbedder
) -> ChromaVectorStore:
    store = ChromaVectorStore.persistent(chroma_dir, TEST_COLLECTION, embedder)
    for document_id, filename, file_type in SAMPLE_FILES:
        pages = extract_pages(SAMPLE_DOCS / filename, file_type)
        chunks = chunk_pages(pages, DEFAULTS.chunk_size, DEFAULTS.chunk_overlap)
        store.add_chunks(document_id, filename, chunks)
    return store


def test_minilm_returns_384_dimensional_unit_vectors(
    embedder: SentenceTransformerEmbedder,
) -> None:
    [vector] = embedder.embed_documents(["Casual leave policy"])

    assert embedder.dimension == 384
    assert len(vector) == 384
    assert sum(v * v for v in vector) == pytest.approx(1.0, abs=1e-4)
    assert embedder.embed_query("Casual leave policy") == pytest.approx(vector)


def test_casual_leave_question_finds_the_casual_leave_chunk(
    sample_store: ChromaVectorStore,
) -> None:
    hits = sample_store.query("How many casual leaves are allowed?", top_k=10)
    print(
        "\nrelevant question:", [(h.filename, h.page, round(h.score, 3)) for h in hits]
    )

    best, runner_up = hits[0], hits[1]
    assert best.filename == "HR_Policy.pdf"
    assert "12 days of casual leave" in best.text
    assert best.score > DEFAULTS.min_relevance
    assert best.score - runner_up.score > 0.05


def test_unrelated_question_scores_below_the_default_threshold(
    sample_store: ChromaVectorStore,
) -> None:
    retriever = DocumentRetriever(
        sample_store, top_k=DEFAULTS.top_k, min_relevance=DEFAULTS.min_relevance
    )
    hits = sample_store.query("What is the capital of France?", top_k=3)
    print(
        "\nunrelated question:", [(h.filename, h.page, round(h.score, 3)) for h in hits]
    )

    assert retriever.search("What is the capital of France?") == []
