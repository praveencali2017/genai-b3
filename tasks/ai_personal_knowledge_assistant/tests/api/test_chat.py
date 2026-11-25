"""POST /chat/ask: grounded answers, sessions, and failures that store nothing."""

from collections.abc import Iterator
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import httpx2
import openai
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import func, select

from ai_personal_knowledge_assistant.api.container import AppContainer, open_container
from ai_personal_knowledge_assistant.api.main import create_app
from ai_personal_knowledge_assistant.core.settings import Settings
from ai_personal_knowledge_assistant.rag.prompts import NOT_FOUND_ANSWER
from ai_personal_knowledge_assistant.storage.chat_history_repository import (
    ChatHistoryRepository,
)
from ai_personal_knowledge_assistant.storage.models import ChatMessage, ChatSession
from ai_personal_knowledge_assistant.storage.records import ChatMessageRecord
from tests.fakes import FakeEmbedder, FakeOpenAI
from tests.rag.conftest import FAKE_EMBEDDER_MIN_RELEVANCE

CASUAL_LEAVE_QUESTION = "How many casual leaves are allowed?"
UNRELATED_QUESTION = "What is the capital of France?"
GROUNDED_REPLY = "Employees get 12 days of casual leave per calendar year [1]."
REQUEST = httpx2.Request("POST", "https://api.openai.com/v1/responses")


@pytest.fixture
def settings(settings: Settings) -> Settings:
    return settings.model_copy(update={"min_relevance": FAKE_EMBEDDER_MIN_RELEVANCE})


@pytest.fixture
def fake_openai() -> FakeOpenAI:
    return FakeOpenAI()


def app_with_client(settings: Settings, client: FakeOpenAI | None) -> FastAPI:
    def open_test_container() -> AbstractContextManager[AppContainer]:
        return open_container(
            settings,
            embedder=FakeEmbedder(),
            openai_client_factory=lambda _settings: cast(openai.OpenAI | None, client),
        )

    return create_app(open_test_container)


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


def ask(client: TestClient, question: str, **fields: Any) -> Response:
    return client.post("/chat/ask", json={"question": question, **fields})


def stored_messages(
    container: AppContainer, session_id: str
) -> list[ChatMessageRecord]:
    with container.session_factory() as session:
        return ChatHistoryRepository(session).recent_messages(UUID(session_id), 100)


def stored_counts(container: AppContainer) -> tuple[int, int]:
    """(chat sessions, chat messages) in the database."""
    with container.session_factory() as session:
        sessions = session.scalar(select(func.count()).select_from(ChatSession))
        messages = session.scalar(select(func.count()).select_from(ChatMessage))
    return sessions or 0, messages or 0


# --- Answers ---


@pytest.mark.usefixtures("hr_policy_id")
def test_a_covered_question_is_answered_with_its_source(
    client: TestClient, container: AppContainer, fake_openai: FakeOpenAI
) -> None:
    fake_openai.responses.script(GROUNDED_REPLY)

    response = ask(client, CASUAL_LEAVE_QUESTION)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["answer"] == GROUNDED_REPLY
    assert body["is_grounded"] is True
    (source,) = body["sources"]
    assert source["filename"] == "HR_Policy.pdf"
    assert source["page"] == 2
    assert source["citation"] == 1
    assert "12 days of casual leave" in source["snippet"]
    assert set(source) == {
        "citation",
        "document_id",
        "filename",
        "page",
        "chunk_index",
        "score",
        "snippet",
    }
    UUID(body["session_id"])

    (call,) = fake_openai.responses.calls
    model_message = call["input"][-1]["content"]
    assert "[1] (HR_Policy.pdf, page 2)" in model_message
    assert "12 days of casual leave" in model_message


@pytest.mark.usefixtures("hr_policy_id")
def test_an_unrelated_question_is_not_found_without_calling_the_model(
    client: TestClient, container: AppContainer, fake_openai: FakeOpenAI
) -> None:
    response = ask(client, UNRELATED_QUESTION)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["answer"] == NOT_FOUND_ANSWER
    assert body["is_grounded"] is False
    assert body["sources"] == []
    assert fake_openai.responses.calls == []
    assert len(stored_messages(container, body["session_id"])) == 2


@pytest.mark.usefixtures("hr_policy_id")
def test_a_not_found_reply_from_the_model_has_no_sources(
    client: TestClient, fake_openai: FakeOpenAI
) -> None:
    fake_openai.responses.script(NOT_FOUND_ANSWER)

    body = ask(client, CASUAL_LEAVE_QUESTION).json()

    assert body["is_grounded"] is False
    assert body["sources"] == []


def test_document_ids_limit_the_search(
    client: TestClient, fake_openai: FakeOpenAI, hr_policy_id: int
) -> None:
    other = ask(client, CASUAL_LEAVE_QUESTION, document_ids=[hr_policy_id + 1]).json()
    assert other["is_grounded"] is False
    assert fake_openai.responses.calls == []

    fake_openai.responses.script(GROUNDED_REPLY)
    own = ask(client, CASUAL_LEAVE_QUESTION, document_ids=[hr_policy_id]).json()
    assert own["is_grounded"] is True
    assert {source["document_id"] for source in own["sources"]} == {hr_policy_id}


# --- Sessions ---


@pytest.mark.usefixtures("hr_policy_id")
def test_the_exchange_is_stored_with_its_sources(
    client: TestClient, container: AppContainer, fake_openai: FakeOpenAI
) -> None:
    fake_openai.responses.script(GROUNDED_REPLY)

    body = ask(client, f"  {CASUAL_LEAVE_QUESTION}  ").json()

    user, assistant = stored_messages(container, body["session_id"])
    assert (user.role, user.content, user.sources) == (
        "user",
        CASUAL_LEAVE_QUESTION,
        None,
    )
    assert assistant.role == "assistant"
    assert assistant.content == GROUNDED_REPLY
    assert assistant.sources == body["sources"]


@pytest.mark.usefixtures("hr_policy_id")
def test_a_follow_up_in_the_same_session_sees_the_history(
    client: TestClient, container: AppContainer, fake_openai: FakeOpenAI
) -> None:
    fake_openai.responses.script(
        GROUNDED_REPLY, "No, it cannot be carried forward [1]."
    )
    first = ask(client, CASUAL_LEAVE_QUESTION).json()

    second = ask(
        client,
        "Can unused casual leave be carried forward?",
        session_id=first["session_id"],
    ).json()

    assert second["session_id"] == first["session_id"]
    assert len(stored_messages(container, first["session_id"])) == 4
    follow_up_input = fake_openai.responses.calls[1]["input"]
    assert follow_up_input[:2] == [
        {"role": "user", "content": CASUAL_LEAVE_QUESTION},
        {"role": "assistant", "content": GROUNDED_REPLY},
    ]
    assert "carried forward?" in follow_up_input[2]["content"]


@pytest.mark.usefixtures("hr_policy_id")
def test_the_first_ask_has_no_history(
    client: TestClient, fake_openai: FakeOpenAI
) -> None:
    fake_openai.responses.script(GROUNDED_REPLY)

    ask(client, CASUAL_LEAVE_QUESTION)

    assert len(fake_openai.responses.calls[0]["input"]) == 1


def test_history_is_capped_at_the_configured_limit(
    settings: Settings, sample_docs_dir: Path
) -> None:
    fake = FakeOpenAI()
    app = app_with_client(settings.model_copy(update={"chat_history_limit": 2}), fake)
    with TestClient(app) as client:
        content = (sample_docs_dir / "HR_Policy.pdf").read_bytes()
        client.post("/documents/upload", files={"file": ("HR_Policy.pdf", content)})
        fake.responses.script("One [1].", "Two [1].", "Three [1].")
        session_id = ask(client, "casual leave one").json()["session_id"]
        ask(client, "casual leave two", session_id=session_id)

        ask(client, "casual leave three", session_id=session_id)

    history = fake.responses.calls[2]["input"][:-1]
    assert history == [
        {"role": "user", "content": "casual leave two"},
        {"role": "assistant", "content": "Two [1]."},
    ]


def test_asks_without_a_session_id_start_new_sessions(client: TestClient) -> None:
    first = ask(client, UNRELATED_QUESTION).json()["session_id"]
    second = ask(client, UNRELATED_QUESTION).json()["session_id"]

    assert first != second


def test_an_unknown_session_id_is_adopted(
    client: TestClient, container: AppContainer
) -> None:
    session_id = str(uuid4())

    body = ask(client, UNRELATED_QUESTION, session_id=session_id).json()

    assert body["session_id"] == session_id
    assert len(stored_messages(container, session_id)) == 2


# --- Failures store nothing ---


@pytest.fixture
def keyless_client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(app_with_client(settings, None)) as client:
        yield client


def test_without_a_key_a_covered_question_is_503(
    keyless_client: TestClient, sample_docs_dir: Path
) -> None:
    content = (sample_docs_dir / "HR_Policy.pdf").read_bytes()
    upload = keyless_client.post(
        "/documents/upload", files={"file": ("HR_Policy.pdf", content)}
    )
    assert upload.status_code == 201, upload.text

    response = ask(keyless_client, CASUAL_LEAVE_QUESTION)

    assert response.status_code == 503
    assert "OPENAI_API_KEY" in response.json()["detail"]
    container: AppContainer = keyless_client.app.state.container  # type: ignore[attr-defined]
    assert stored_counts(container) == (0, 0)


def test_without_a_key_an_unrelated_question_is_still_answered(
    keyless_client: TestClient,
) -> None:
    response = ask(keyless_client, UNRELATED_QUESTION)

    assert response.status_code == 200
    assert response.json()["answer"] == NOT_FOUND_ANSWER


@pytest.mark.parametrize(
    "error",
    [
        openai.APIConnectionError(request=REQUEST),
        openai.APIStatusError(
            "Service unavailable",
            response=httpx2.Response(503, request=REQUEST),
            body=None,
        ),
    ],
    ids=["connection", "status"],
)
@pytest.mark.usefixtures("hr_policy_id")
def test_an_openai_failure_is_502_and_stores_nothing(
    client: TestClient,
    container: AppContainer,
    fake_openai: FakeOpenAI,
    error: Exception,
) -> None:
    fake_openai.responses.script(GROUNDED_REPLY, error)
    session_id = ask(client, CASUAL_LEAVE_QUESTION).json()["session_id"]
    before = stored_counts(container)

    response = ask(client, CASUAL_LEAVE_QUESTION, session_id=session_id)

    assert response.status_code == 502
    assert "unavailable" in response.json()["detail"]
    assert stored_counts(container) == before == (1, 2)


@pytest.mark.usefixtures("hr_policy_id")
def test_a_failed_first_ask_creates_no_session(
    client: TestClient, container: AppContainer, fake_openai: FakeOpenAI
) -> None:
    fake_openai.responses.script(openai.APIConnectionError(request=REQUEST))

    assert ask(client, CASUAL_LEAVE_QUESTION).status_code == 502
    assert stored_counts(container) == (0, 0)


# --- Validation ---


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"question": ""},
        {"question": "   \n"},
        {"question": "x" * 2001},
        {"question": "Hi", "session_id": "not-a-uuid"},
        {"question": "Hi", "document_ids": [0]},
    ],
    ids=["missing", "empty", "blank", "too-long", "bad-session", "bad-document-id"],
)
def test_invalid_requests_are_422(
    client: TestClient,
    container: AppContainer,
    fake_openai: FakeOpenAI,
    body: dict[str, Any],
) -> None:
    assert client.post("/chat/ask", json=body).status_code == 422
    assert fake_openai.responses.calls == []
    assert stored_counts(container) == (0, 0)


def test_a_question_of_the_maximum_length_is_accepted(client: TestClient) -> None:
    assert ask(client, "x" * 2000).status_code == 200
