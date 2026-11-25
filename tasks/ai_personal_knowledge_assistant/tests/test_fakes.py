"""The fake embedder's similarity property, which retrieval tests rely on."""

import math

import pytest

from tests.fakes import FakeEmbedder


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


@pytest.fixture
def embedder() -> FakeEmbedder:
    return FakeEmbedder()


def test_vectors_are_unit_length_with_the_declared_dimension(
    embedder: FakeEmbedder,
) -> None:
    for text in ["casual leave policy", "", "the and of"]:
        vector = embedder.embed_query(text)
        assert len(vector) == embedder.dimension
        assert math.sqrt(sum(v * v for v in vector)) == pytest.approx(1.0)


def test_same_text_gives_the_same_vector(embedder: FakeEmbedder) -> None:
    assert embedder.embed_query("Casual Leave") == FakeEmbedder().embed_query(
        "casual leave"
    )


def test_shared_words_score_high_and_unrelated_texts_score_zero(
    embedder: FakeEmbedder,
) -> None:
    question = embedder.embed_query("How many casual leaves are allowed?")
    related, unrelated = embedder.embed_documents(
        [
            "Employees get twelve casual leaves allowed per year.",
            "What is the capital of France?",
        ]
    )

    assert cosine(question, related) > 0.5
    assert cosine(question, unrelated) == pytest.approx(0.0)


def test_document_and_query_embeddings_agree(embedder: FakeEmbedder) -> None:
    [document] = embedder.embed_documents(["travel desk"])

    assert document == embedder.embed_query("travel desk")
    assert embedder.document_batch_sizes == [1]
