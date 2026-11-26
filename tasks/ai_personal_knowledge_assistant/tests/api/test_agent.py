"""POST /agent/chat: tool use end to end, sessions, and failures that store nothing."""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import Response

from ai_personal_knowledge_assistant.agent.tools import (
    ANSWER_FROM_DOCUMENTS,
    LIST_DOCUMENTS,
)
from ai_personal_knowledge_assistant.api.container import AppContainer
from ai_personal_knowledge_assistant.core.settings import Settings
from tests.api.conftest import app_with_client, stored_counts, stored_messages
from tests.fakes import FakeOpenAI, function_call, tool_calls_response
from tests.rag.conftest import FAKE_EMBEDDER_MIN_RELEVANCE

LIST_MESSAGE = "List my uploaded documents."
LIST_REPLY = "You have one document: HR_Policy.pdf (3 pages)."
LEAVE_QUESTION = "How many casual leaves are allowed?"
GROUNDED_REPLY = "Employees get 12 days of casual leave per calendar year [1]."
AGENT_LEAVE_REPLY = "You get 12 days of casual leave a year (HR_Policy.pdf, page 2)."


@pytest.fixture
def settings(settings: Settings) -> Settings:
    return settings.model_copy(update={"min_relevance": FAKE_EMBEDDER_MIN_RELEVANCE})


@pytest.fixture
def fake_openai() -> FakeOpenAI:
    return FakeOpenAI()


@pytest.fixture
def app(settings: Settings, fake_openai: FakeOpenAI) -> FastAPI:
    return app_with_client(settings, fake_openai)


@pytest.fixture
def hr_policy_id(client: TestClient, sample_docs_dir: Path) -> int:
    content = (sample_docs_dir / "HR_Policy.pdf").read_bytes()
    response = client.post(
        "/documents/upload", files={"file": ("HR_Policy.pdf", content)}
    )
    assert response.status_code == 201, response.text
    return cast(int, response.json()["id"])


def chat(client: TestClient, message: str, **fields: Any) -> Response:
    return client.post("/agent/chat", json={"message": message, **fields})


def script_listing(fake_openai: FakeOpenAI) -> None:
    fake_openai.responses.script(
        tool_calls_response(function_call(LIST_DOCUMENTS, {}, call_id="call_list")),
        LIST_REPLY,
    )


# --- Tool use ---


def test_listing_documents_uses_list_documents(
    client: TestClient, fake_openai: FakeOpenAI, hr_policy_id: int
) -> None:
    script_listing(fake_openai)

    response = chat(client, LIST_MESSAGE)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["reply"] == LIST_REPLY
    assert body["tool_calls"] == [
        {
            "tool": LIST_DOCUMENTS,
            "arguments": {},
            "is_error": False,
            "summary": "1 document",
        }
    ]
    assert body["sources"] == []

    second_input = fake_openai.responses.calls[1]["input"]
    (output,) = [
        item
        for item in second_input
        if isinstance(item, dict) and item.get("type") == "function_call_output"
    ]
    assert output["call_id"] == "call_list"
    (listed,) = json.loads(output["output"])
    assert (listed["id"], listed["filename"]) == (hr_policy_id, "HR_Policy.pdf")


@pytest.mark.usefixtures("hr_policy_id")
def test_a_leave_question_uses_answer_from_documents_and_returns_its_sources(
    client: TestClient, fake_openai: FakeOpenAI
) -> None:
    fake_openai.responses.script(
        tool_calls_response(
            function_call(
                ANSWER_FROM_DOCUMENTS,
                {"question": LEAVE_QUESTION, "document_ids": None},
            )
        ),
        GROUNDED_REPLY,  # the grounded answerer's model call
        AGENT_LEAVE_REPLY,
    )

    body = chat(client, LEAVE_QUESTION).json()

    assert body["reply"] == AGENT_LEAVE_REPLY
    assert [call["tool"] for call in body["tool_calls"]] == [ANSWER_FROM_DOCUMENTS]
    assert body["tool_calls"][0]["summary"] == "grounded answer with 1 source"
    (source,) = body["sources"]
    assert (source["filename"], source["page"], source["citation"]) == (
        "HR_Policy.pdf",
        2,
        1,
    )
    assert "12 days of casual leave" in source["snippet"]


def test_a_bad_tool_call_is_reported_not_fatal(
    client: TestClient, fake_openai: FakeOpenAI
) -> None:
    fake_openai.responses.script(
        tool_calls_response(function_call("delete_documents", "{not json")),
        "I can't delete documents.",
    )

    response = chat(client, "Delete everything.")

    assert response.status_code == 200, response.text
    (tool_call,) = response.json()["tool_calls"]
    assert tool_call == {
        "tool": "delete_documents",
        "arguments": "{not json",
        "is_error": True,
        "summary": "Unknown tool 'delete_documents'.",
    }


# --- Sessions ---


@pytest.mark.usefixtures("hr_policy_id")
def test_the_exchange_is_stored_with_tool_calls_and_sources(
    client: TestClient, container: AppContainer, fake_openai: FakeOpenAI
) -> None:
    fake_openai.responses.script(
        tool_calls_response(
            function_call(ANSWER_FROM_DOCUMENTS, {"question": LEAVE_QUESTION})
        ),
        GROUNDED_REPLY,
        AGENT_LEAVE_REPLY,
    )

    body = chat(client, f"  {LEAVE_QUESTION} ").json()

    user, assistant = stored_messages(container, body["session_id"])
    assert (user.role, user.content, user.tool_calls) == ("user", LEAVE_QUESTION, None)
    assert (assistant.role, assistant.content) == ("assistant", AGENT_LEAVE_REPLY)
    assert assistant.tool_calls == body["tool_calls"]
    assert assistant.sources == body["sources"]


@pytest.mark.usefixtures("hr_policy_id")
def test_a_follow_up_in_the_same_session_sees_the_history(
    client: TestClient, container: AppContainer, fake_openai: FakeOpenAI
) -> None:
    script_listing(fake_openai)
    first = chat(client, LIST_MESSAGE).json()
    fake_openai.responses.script("It has 3 pages.")

    second = chat(
        client, "How many pages does it have?", session_id=first["session_id"]
    )

    assert second.status_code == 200, second.text
    assert second.json()["session_id"] == first["session_id"]
    assert fake_openai.responses.calls[2]["input"] == [
        {"role": "user", "content": LIST_MESSAGE},
        {"role": "assistant", "content": LIST_REPLY},
        {"role": "user", "content": "How many pages does it have?"},
    ]
    messages = stored_messages(container, first["session_id"])
    assert [message.role for message in messages] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    assert messages[1].tool_calls == first["tool_calls"]
    assert messages[3].tool_calls == []


def test_chats_without_a_session_id_start_new_sessions(
    client: TestClient, fake_openai: FakeOpenAI
) -> None:
    fake_openai.responses.script("Hello.", "Hello again.")

    first = chat(client, "Hi").json()["session_id"]
    second = chat(client, "Hi").json()["session_id"]

    assert first != second


# --- Failures store nothing ---


def test_the_step_limit_is_502_and_stores_nothing(
    client: TestClient,
    container: AppContainer,
    fake_openai: FakeOpenAI,
    settings: Settings,
) -> None:
    rounds = settings.agent_max_rounds
    fake_openai.responses.script(
        *(
            tool_calls_response(function_call(LIST_DOCUMENTS, {}, call_id=f"c{n}"))
            for n in range(rounds)
        )
    )

    response = chat(client, LIST_MESSAGE)

    assert response.status_code == 502
    assert response.json() == {
        "detail": f"The assistant didn't finish within {rounds} steps. "
        "Try a simpler or more specific question."
    }
    assert len(fake_openai.responses.calls) == rounds
    assert stored_counts(container) == (0, 0)


@pytest.fixture
def keyless_client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(app_with_client(settings, None)) as client:
        yield client


def test_without_a_key_the_agent_is_503_and_stores_nothing(
    keyless_client: TestClient,
) -> None:
    response = chat(keyless_client, LIST_MESSAGE)

    assert response.status_code == 503
    assert "OPENAI_API_KEY" in response.json()["detail"]
    container: AppContainer = keyless_client.app.state.container  # type: ignore[attr-defined]
    assert stored_counts(container) == (0, 0)


# --- Validation ---


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"message": ""},
        {"message": "  \t "},
        {"message": "x" * 2001},
        {"message": "Hi", "session_id": "not-a-uuid"},
    ],
    ids=["missing", "empty", "blank", "too-long", "bad-session"],
)
def test_invalid_requests_are_422(
    client: TestClient,
    container: AppContainer,
    fake_openai: FakeOpenAI,
    body: dict[str, Any],
) -> None:
    assert client.post("/agent/chat", json=body).status_code == 422
    assert fake_openai.responses.calls == []
    assert stored_counts(container) == (0, 0)
