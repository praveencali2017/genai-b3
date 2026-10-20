"""Tests for the /chat endpoint, ChatService, the model adapters and provider selection.

No OpenAI key, Ollama server or network needed.
"""

from collections.abc import Iterator
from types import SimpleNamespace

import httpx2
import openai
import pytest
from fastapi.testclient import TestClient

import chat
from chat import ChatService
from chat_models import ChatProviderError, OllamaChatModel, OpenAIResponsesModel
from main import app

CHAT_ENV_VARS = (
    "OPENAI_API_KEY",
    "OPENAI_MODEL",
    "OLLAMA_ENABLED",
    "OLLAMA_BASE_URL",
    "OLLAMA_MODEL",
)


def connection_error() -> openai.APIConnectionError:
    return openai.APIConnectionError(request=httpx2.Request("POST", "http://localhost/v1"))


class FakeChatModel:
    """Stands in for a ChatModel adapter: records calls, returns or raises a preset result."""

    model = "test-model"

    def __init__(self, text: str = "", error: Exception | None = None) -> None:
        self.text = text
        self.error = error
        self.calls: list[tuple[str, str]] = []

    def complete(self, instructions: str, prompt: str) -> str:
        self.calls.append((instructions, prompt))
        if self.error is not None:
            raise self.error
        return self.text


class FakeEndpoint:
    """Stands in for client.responses or client.chat.completions."""

    def __init__(self, result: object = None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls: list[dict] = []

    def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.result


@pytest.fixture
def no_chat_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(chat, "find_env_file", lambda: None)
    for name in CHAT_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def chat_model() -> FakeChatModel:
    return FakeChatModel(text="  Paris is the capital of France.  ")


@pytest.fixture
def client(chat_model: FakeChatModel) -> Iterator[TestClient]:
    app.dependency_overrides[chat.get_chat_service] = lambda: ChatService(chat_model)
    yield TestClient(app)
    app.dependency_overrides.clear()


# ---------- Endpoint ----------


def test_chat_returns_the_trimmed_answer_and_model(
    client: TestClient, chat_model: FakeChatModel
) -> None:
    response = client.post("/chat", json={"message": "  Capital of France?  "})

    assert response.status_code == 200
    assert response.json() == {
        "reply": "Paris is the capital of France.",
        "model": "test-model",
    }
    ((instructions, prompt),) = chat_model.calls
    assert prompt == "Capital of France?"
    assert "briefly" in instructions


@pytest.mark.parametrize(
    "body", [{}, {"message": ""}, {"message": "   "}, {"message": "x" * 2001}]
)
def test_chat_rejects_invalid_messages(
    client: TestClient, chat_model: FakeChatModel, body: dict
) -> None:
    assert client.post("/chat", json=body).status_code == 422
    assert chat_model.calls == []


def test_chat_reports_provider_failure_as_502(
    client: TestClient, chat_model: FakeChatModel
) -> None:
    chat_model.error = ChatProviderError("boom")

    response = client.post("/chat", json={"message": "Hi"})

    assert response.status_code == 502
    assert response.json() == {
        "detail": "The assistant couldn't answer right now. Please try again."
    }


def test_chat_reports_empty_answer_as_502(
    client: TestClient, chat_model: FakeChatModel
) -> None:
    chat_model.text = "   "

    response = client.post("/chat", json={"message": "Hi"})

    assert response.status_code == 502
    assert response.json() == {"detail": "The assistant returned an empty answer."}


@pytest.mark.usefixtures("no_chat_env")
def test_chat_without_any_provider_returns_503() -> None:
    response = TestClient(app).post("/chat", json={"message": "Hi"})

    assert response.status_code == 503
    assert "OPENAI_API_KEY" in response.json()["detail"]


@pytest.mark.usefixtures("no_chat_env")
def test_chat_with_invalid_ollama_flag_returns_503(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_ENABLED", "maybe")

    response = TestClient(app).post("/chat", json={"message": "Hi"})

    assert response.status_code == 503
    assert "OLLAMA_ENABLED" in response.json()["detail"]


# ---------- Adapters ----------


def test_openai_adapter_calls_the_responses_api() -> None:
    responses = FakeEndpoint(result=SimpleNamespace(output_text="Hello"))
    adapter = OpenAIResponsesModel(SimpleNamespace(responses=responses), model="gpt-x")

    assert adapter.complete("Be brief.", "Hi") == "Hello"
    assert adapter.model == "gpt-x"
    assert responses.calls == [{"model": "gpt-x", "instructions": "Be brief.", "input": "Hi"}]


def test_openai_adapter_wraps_sdk_errors() -> None:
    responses = FakeEndpoint(error=connection_error())
    adapter = OpenAIResponsesModel(SimpleNamespace(responses=responses), model="gpt-x")

    with pytest.raises(ChatProviderError):
        adapter.complete("Be brief.", "Hi")


def ollama_adapter_with(completions: FakeEndpoint) -> OllamaChatModel:
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return OllamaChatModel(client, model="llama3.2")


def completion_with(content: str | None) -> SimpleNamespace:
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def test_ollama_adapter_calls_chat_completions() -> None:
    completions = FakeEndpoint(result=completion_with("Hello"))
    adapter = ollama_adapter_with(completions)

    assert adapter.complete("Be brief.", "Hi") == "Hello"
    assert adapter.model == "llama3.2"
    assert completions.calls == [
        {
            "model": "llama3.2",
            "messages": [
                {"role": "system", "content": "Be brief."},
                {"role": "user", "content": "Hi"},
            ],
        }
    ]


@pytest.mark.parametrize(
    "result", [completion_with(None), SimpleNamespace(choices=[])], ids=["null", "no-choices"]
)
def test_ollama_adapter_returns_empty_text_for_missing_content(result: object) -> None:
    assert ollama_adapter_with(FakeEndpoint(result=result)).complete("x", "y") == ""


def test_ollama_adapter_wraps_sdk_errors() -> None:
    adapter = ollama_adapter_with(FakeEndpoint(error=connection_error()))

    with pytest.raises(ChatProviderError):
        adapter.complete("Be brief.", "Hi")


def test_service_answer_carries_the_adapter_model() -> None:
    answer = ChatService(FakeChatModel(text=" Hi there ")).answer("Hi")

    assert answer == chat.ChatAnswer(text="Hi there", model="test-model")


# ---------- Settings and provider selection ----------


@pytest.mark.usefixtures("no_chat_env")
def test_openai_settings_default_the_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", " sk-test ")

    assert chat.load_chat_settings() == chat.OpenAISettings(
        api_key="sk-test", model=chat.DEFAULT_OPENAI_MODEL
    )


@pytest.mark.usefixtures("no_chat_env")
def test_openai_settings_use_a_custom_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-5.4-nano")

    assert chat.load_chat_settings().model == "gpt-5.4-nano"


@pytest.mark.usefixtures("no_chat_env")
def test_blank_api_key_counts_as_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "  ")

    with pytest.raises(chat.ChatNotConfiguredError):
        chat.load_chat_settings()


@pytest.mark.usefixtures("no_chat_env")
@pytest.mark.parametrize("flag", ["true", " TRUE ", "1", "yes", "on"])
def test_ollama_selected_with_defaults_and_no_api_key(
    monkeypatch: pytest.MonkeyPatch, flag: str
) -> None:
    monkeypatch.setenv("OLLAMA_ENABLED", flag)

    assert chat.load_chat_settings() == chat.OllamaSettings(
        base_url=chat.DEFAULT_OLLAMA_BASE_URL, model=chat.DEFAULT_OLLAMA_MODEL
    )


@pytest.mark.usefixtures("no_chat_env")
def test_ollama_settings_use_custom_url_and_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_ENABLED", "true")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://gpu-box:11434/v1")
    monkeypatch.setenv("OLLAMA_MODEL", "llama3.2:1b")

    assert chat.load_chat_settings() == chat.OllamaSettings(
        base_url="http://gpu-box:11434/v1", model="llama3.2:1b"
    )


@pytest.mark.usefixtures("no_chat_env")
@pytest.mark.parametrize("flag", ["false", "0", "no", "off", ""])
def test_openai_selected_when_ollama_disabled(
    monkeypatch: pytest.MonkeyPatch, flag: str
) -> None:
    monkeypatch.setenv("OLLAMA_ENABLED", flag)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    assert isinstance(chat.load_chat_settings(), chat.OpenAISettings)


@pytest.mark.usefixtures("no_chat_env")
def test_invalid_ollama_flag_fails_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_ENABLED", "maybe")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    with pytest.raises(chat.ChatNotConfiguredError, match="OLLAMA_ENABLED"):
        chat.load_chat_settings()


@pytest.mark.parametrize(
    ("settings", "adapter_type"),
    [
        (chat.OllamaSettings(base_url="http://localhost:11434/v1", model="llama3.2"), OllamaChatModel),
        (chat.OpenAISettings(api_key="sk-test", model="gpt-x"), OpenAIResponsesModel),
    ],
    ids=["ollama", "openai"],
)
def test_build_chat_model_picks_the_matching_adapter(
    settings: chat.ChatSettings, adapter_type: type
) -> None:
    chat_model = chat.build_chat_model(settings)

    assert isinstance(chat_model, adapter_type)
    assert chat_model.model == settings.model
