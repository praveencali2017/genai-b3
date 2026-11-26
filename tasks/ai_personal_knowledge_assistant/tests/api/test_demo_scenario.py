"""The brief's demo, step by step, through the HTTP API.

Success criteria: it works end to end, the evidence comes from ChromaDB, it
refuses to invent answers the documents don't hold, and it shows which tools
the agent used. Each test is one of those criteria on the demo's inputs; the
model is scripted, everything else (ingestion, Chroma, SQLite) is real.
"""

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ai_personal_knowledge_assistant.agent.tools import LIST_DOCUMENTS
from ai_personal_knowledge_assistant.api.container import AppContainer
from ai_personal_knowledge_assistant.core.settings import Settings
from ai_personal_knowledge_assistant.rag.prompts import NOT_FOUND_ANSWER
from tests.api.conftest import app_with_client
from tests.fakes import FakeOpenAI, function_call, tool_calls_response
from tests.rag.conftest import FAKE_EMBEDDER_MIN_RELEVANCE

CASUAL_LEAVE_QUESTION = "How many casual leaves are allowed?"
CASUAL_LEAVE_ANSWER = "Employees get 12 days of casual leave per calendar year [1]."
UNRELATED_QUESTION = "What is the capital of France?"
LIST_MESSAGE = "List my uploaded documents."
LIST_REPLY = "You have one document: HR_Policy.pdf."


@pytest.fixture
def settings(settings: Settings) -> Settings:
    return settings.model_copy(update={"min_relevance": FAKE_EMBEDDER_MIN_RELEVANCE})


@pytest.fixture
def fake_openai() -> FakeOpenAI:
    return FakeOpenAI()


@pytest.fixture
def app(settings: Settings, fake_openai: FakeOpenAI) -> FastAPI:
    return app_with_client(settings, fake_openai)


@pytest.fixture(autouse=True)
def upload_hr_policy(client: TestClient, sample_docs_dir: Path) -> None:
    """Demo step 1: upload HR_Policy.pdf."""
    content = (sample_docs_dir / "HR_Policy.pdf").read_bytes()
    response = client.post(
        "/documents/upload", files={"file": ("HR_Policy.pdf", content)}
    )
    assert response.status_code == 201, response.text
    assert response.json()["filename"] == "HR_Policy.pdf"


def test_the_casual_leave_question_is_answered_from_hr_policy_page_2(
    client: TestClient, fake_openai: FakeOpenAI
) -> None:
    fake_openai.responses.script(CASUAL_LEAVE_ANSWER)

    response = client.post("/chat/ask", json={"question": CASUAL_LEAVE_QUESTION})

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["answer"], body["is_grounded"]) == (CASUAL_LEAVE_ANSWER, True)
    (source,) = body["sources"]
    assert (source["filename"], source["page"]) == ("HR_Policy.pdf", 2)


def test_the_evidence_given_to_the_model_comes_from_chromadb(
    client: TestClient, container: AppContainer, fake_openai: FakeOpenAI
) -> None:
    fake_openai.responses.script(CASUAL_LEAVE_ANSWER)

    body = client.post("/chat/ask", json={"question": CASUAL_LEAVE_QUESTION}).json()

    (source,) = body["sources"]
    (stored,) = [
        chunk
        for chunk in container.vector_store.query(CASUAL_LEAVE_QUESTION, top_k=10)
        if chunk.chunk_index == source["chunk_index"]
    ]
    (call,) = fake_openai.responses.calls
    prompt = call["input"][-1]["content"]
    assert f"[1] (HR_Policy.pdf, page 2) {stored.text}" in prompt


def test_an_unrelated_question_is_refused_without_calling_the_model(
    client: TestClient, fake_openai: FakeOpenAI
) -> None:
    response = client.post("/chat/ask", json={"question": UNRELATED_QUESTION})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["answer"] == NOT_FOUND_ANSWER
    assert body["is_grounded"] is False
    assert body["sources"] == []
    assert fake_openai.responses.calls == []


def test_the_agent_shows_it_used_list_documents(
    client: TestClient, fake_openai: FakeOpenAI
) -> None:
    fake_openai.responses.script(
        tool_calls_response(function_call(LIST_DOCUMENTS, {})), LIST_REPLY
    )

    response = client.post("/agent/chat", json={"message": LIST_MESSAGE})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["reply"] == LIST_REPLY
    assert [call["tool"] for call in body["tool_calls"]] == [LIST_DOCUMENTS]
    assert body["tool_calls"][0]["is_error"] is False
