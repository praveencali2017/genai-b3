"""DocumentRetriever: the relevance threshold and per-call overrides."""

import pytest

from ai_personal_knowledge_assistant.ingestion.chunker import Chunk
from ai_personal_knowledge_assistant.rag.retriever import DocumentRetriever
from ai_personal_knowledge_assistant.vectorstore.chroma_store import ChromaVectorStore

LEAVE_CHUNKS = [
    Chunk(index=0, text="Casual leave: twelve days per calendar year.", page=2),
    Chunk(index=1, text="Casual leave cannot be carried forward.", page=2),
    Chunk(index=2, text="Sick leave needs a medical certificate.", page=2),
    Chunk(index=3, text="Office hours run nine to five.", page=3),
]
TRAVEL_CHUNKS = [
    Chunk(index=0, text="Casual dress is fine when travelling.", page=None)
]


@pytest.fixture
def store(vector_store: ChromaVectorStore) -> ChromaVectorStore:
    vector_store.add_chunks(1, "HR_Policy.pdf", LEAVE_CHUNKS)
    vector_store.add_chunks(2, "Travel_Policy.txt", TRAVEL_CHUNKS)
    return vector_store


def test_hits_below_the_threshold_are_dropped(store: ChromaVectorStore) -> None:
    retriever = DocumentRetriever(store, top_k=10, min_relevance=0.3)

    hits = retriever.search("casual leave days")

    assert hits
    assert all(hit.score >= 0.3 for hit in hits)
    assert "Office hours run nine to five." not in {hit.text for hit in hits}
    assert len(hits) < len(store.query("casual leave days", top_k=10))


def test_a_question_with_no_relevant_chunk_returns_nothing(
    store: ChromaVectorStore,
) -> None:
    retriever = DocumentRetriever(store, top_k=5, min_relevance=0.3)

    assert retriever.search("What is the capital of France?") == []


def test_threshold_zero_keeps_every_hit(store: ChromaVectorStore) -> None:
    retriever = DocumentRetriever(store, top_k=10, min_relevance=0.0)

    assert len(retriever.search("casual leave")) == len(LEAVE_CHUNKS) + 1


def test_hit_exactly_at_the_threshold_is_kept(store: ChromaVectorStore) -> None:
    best = store.query("office hours", top_k=1)[0]
    retriever = DocumentRetriever(store, top_k=1, min_relevance=best.score)

    assert retriever.search("office hours") == [best]


def test_default_top_k_caps_the_hits(store: ChromaVectorStore) -> None:
    retriever = DocumentRetriever(store, top_k=1, min_relevance=0.0)

    assert len(retriever.search("casual leave")) == 1


def test_top_k_override_replaces_the_default(store: ChromaVectorStore) -> None:
    retriever = DocumentRetriever(store, top_k=1, min_relevance=0.0)

    assert len(retriever.search("casual leave", top_k=3)) == 3


def test_document_ids_are_passed_through(store: ChromaVectorStore) -> None:
    retriever = DocumentRetriever(store, top_k=10, min_relevance=0.1)

    hits = retriever.search("casual", document_ids=[2])

    assert [hit.document_id for hit in hits] == [2]


def test_hits_stay_sorted_best_first(store: ChromaVectorStore) -> None:
    retriever = DocumentRetriever(store, top_k=10, min_relevance=0.0)

    scores = [hit.score for hit in retriever.search("casual leave year")]

    assert scores == sorted(scores, reverse=True)


@pytest.mark.parametrize("question", ["", "   ", "\n\t"])
def test_blank_question_is_rejected(store: ChromaVectorStore, question: str) -> None:
    retriever = DocumentRetriever(store, top_k=5, min_relevance=0.3)

    with pytest.raises(ValueError, match="blank"):
        retriever.search(question)


@pytest.mark.parametrize(
    ("top_k", "min_relevance", "message"),
    [(0, 0.3, "top_k"), (5, -0.1, "min_relevance"), (5, 1.1, "min_relevance")],
)
def test_invalid_configuration_is_rejected(
    vector_store: ChromaVectorStore, top_k: int, min_relevance: float, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        DocumentRetriever(vector_store, top_k=top_k, min_relevance=min_relevance)
