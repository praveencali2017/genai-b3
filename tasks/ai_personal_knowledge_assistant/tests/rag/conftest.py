"""Helpers for answering tests; the HR policy and fake model fixtures are in
`tests/conftest.py`, shared with the agent tests."""

from typing import cast

import openai

from ai_personal_knowledge_assistant.rag.answerer import GroundedAnswerer
from ai_personal_knowledge_assistant.rag.retriever import DocumentRetriever
from ai_personal_knowledge_assistant.vectorstore.chroma_store import ChromaVectorStore
from tests.fakes import FakeOpenAI

TEST_MODEL = "test-model"
# FakeEmbedder scores are word overlap, not MiniLM similarity: "How many casual
# leaves are allowed?" shares only "casual" with the leave chunk (score ~0.11),
# while an unrelated question scores 0. This threshold keeps that contrast.
FAKE_EMBEDDER_MIN_RELEVANCE = 0.05


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
