"""The agent's toolkit on the real HR policy, real Chroma and a fake model."""

from typing import cast

import openai
import pytest
from sqlalchemy.orm import Session, sessionmaker

from ai_personal_knowledge_assistant.agent.runner import SingleAgent
from ai_personal_knowledge_assistant.agent.tools import AgentToolkit
from ai_personal_knowledge_assistant.rag.retriever import DocumentRetriever
from ai_personal_knowledge_assistant.vectorstore.chroma_store import ChromaVectorStore
from tests.fakes import FakeOpenAI
from tests.rag.conftest import FAKE_EMBEDDER_MIN_RELEVANCE, TEST_MODEL, make_answerer

MAX_ROUNDS = 4


def make_toolkit(
    session_factory: sessionmaker[Session],
    store: ChromaVectorStore,
    client: FakeOpenAI | None,
) -> AgentToolkit:
    return AgentToolkit(
        session_factory,
        DocumentRetriever(store, top_k=5, min_relevance=FAKE_EMBEDDER_MIN_RELEVANCE),
        make_answerer(store, client),
    )


@pytest.fixture
def toolkit(
    session_factory: sessionmaker[Session],
    vector_store: ChromaVectorStore,
    fake_openai: FakeOpenAI,
) -> AgentToolkit:
    return make_toolkit(session_factory, vector_store, fake_openai)


def make_agent(
    client: FakeOpenAI | None, toolkit: AgentToolkit, max_rounds: int = MAX_ROUNDS
) -> SingleAgent:
    return SingleAgent(
        cast(openai.OpenAI | None, client), TEST_MODEL, toolkit, max_rounds
    )
