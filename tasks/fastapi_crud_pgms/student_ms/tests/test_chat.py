"""Tests for the /chat endpoint and ChatService. No OpenAI key or network needed."""

from collections.abc import Iterator
from types import SimpleNamespace

import httpx2
import openai
import pytest
from fastapi.testclient import TestClient

import chat
from chat import ChatService
from main import app


class FakeResponses:
    """Stands in for client.responses: records calls, returns or raises a preset result."""

    def __init__(self, output_text: str = "", error: Exception | None = None) -> None:
        self.output_text = output_text
        self.error = error
        self.calls: list[dict] = []

    def create(self, **kwargs: object) -> SimpleNamespace:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(id="resp_test", output_text=self.output_text)


def service_with(responses: FakeResponses) -> ChatService:
    return ChatService(SimpleNamespace(responses=responses), model="test-model")


@pytest.fixture
def responses() -> FakeResponses:
    return FakeResponses(output_text="  Paris is the capital of France.  ")


@pytest.fixture
def client(responses: FakeResponses) -> Iterator[TestClient]:
    app.dependency_overrides[chat.get_chat_service] = lambda: service_with(responses)
    yield TestClient(app)
    app.dependency_overrides.clear()


# ---------- Endpoint ----------


def test_chat_returns_the_trimmed_answer(
    client: TestClient, responses: FakeResponses
) -> None:
    response = client.post("/chat", json={"message": "  Capital of France?  "})

    assert response.status_code == 200
    assert response.json() == {"reply": "Paris is the capital of France."}
    (call,) = responses.calls
    assert call["input"] == "Capital of France?"
    assert call["model"] == "test-model"
    assert "briefly" in call["instructions"]


@pytest.mark.parametrize(
    "body", [{}, {"message": ""}, {"message": "   "}, {"message": "x" * 2001}]
)
def test_chat_rejects_invalid_messages(
    client: TestClient, responses: FakeResponses, body: dict
) -> None:
    assert client.post("/chat", json=body).status_code == 422
    assert responses.calls == []


def test_chat_reports_upstream_failure_as_502(
    client: TestClient, responses: FakeResponses
) -> None:
    responses.error = openai.APIConnectionError(
        request=httpx2.Request("POST", "https://api.openai.com/v1/responses")
    )

    response = client.post("/chat", json={"message": "Hi"})

    assert response.status_code == 502
    assert response.json() == {
        "detail": "The assistant couldn't answer right now. Please try again."
    }


def test_chat_reports_empty_answer_as_502(
    client: TestClient, responses: FakeResponses
) -> None:
    responses.output_text = "   "

    response = client.post("/chat", json={"message": "Hi"})

    assert response.status_code == 502
    assert response.json() == {"detail": "The assistant returned an empty answer."}


def test_chat_without_api_key_returns_503(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(chat, "find_env_file", lambda: None)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    response = TestClient(app).post("/chat", json={"message": "Hi"})

    assert response.status_code == 503
    assert "OPENAI_API_KEY" in response.json()["detail"]


# ---------- Settings ----------


@pytest.fixture
def no_env_file(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(chat, "find_env_file", lambda: None)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)


@pytest.mark.usefixtures("no_env_file")
def test_settings_default_the_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", " sk-test ")

    assert chat.load_chat_settings() == chat.ChatSettings(
        api_key="sk-test", model=chat.DEFAULT_MODEL
    )


@pytest.mark.usefixtures("no_env_file")
def test_settings_use_a_custom_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-5.4-nano")

    assert chat.load_chat_settings().model == "gpt-5.4-nano"


@pytest.mark.usefixtures("no_env_file")
def test_blank_api_key_counts_as_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "  ")

    with pytest.raises(chat.ChatNotConfiguredError):
        chat.load_chat_settings()
