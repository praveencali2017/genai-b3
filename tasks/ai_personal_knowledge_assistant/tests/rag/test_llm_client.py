"""The OpenAI boundary: client construction and error mapping."""

from types import SimpleNamespace
from typing import cast

import httpx2
import openai
import pytest

from ai_personal_knowledge_assistant.core.errors import (
    AssistantNotConfiguredError,
    AssistantUnavailableError,
)
from ai_personal_knowledge_assistant.core.settings import Settings
from ai_personal_knowledge_assistant.rag.llm_client import (
    OPENAI_MAX_RETRIES,
    OPENAI_TIMEOUT_SECONDS,
    build_openai_client,
    complete_text,
    final_message_text,
    require_client,
)
from tests.fakes import FakeOpenAI

REQUEST = httpx2.Request("POST", "https://api.openai.com/v1/responses")
MESSAGES = [{"role": "user", "content": "Hi"}]


def status_error(error_class: type[openai.APIStatusError], code: int) -> Exception:
    response = httpx2.Response(code, request=REQUEST)
    return error_class(
        "Incorrect API key provided: sk-abc***xyz", response=response, body=None
    )


def complete(fake: FakeOpenAI) -> str:
    return complete_text(cast(openai.OpenAI, fake), "test-model", "Be brief.", MESSAGES)


def test_no_key_builds_no_client() -> None:
    assert build_openai_client(Settings(openai_api_key=None)) is None


def test_a_key_builds_a_client_with_timeout_and_retries() -> None:
    client = build_openai_client(Settings(openai_api_key="sk-test"))

    assert isinstance(client, openai.OpenAI)
    assert client.api_key == "sk-test"
    assert client.timeout == OPENAI_TIMEOUT_SECONDS
    assert client.max_retries == OPENAI_MAX_RETRIES


def test_require_client_explains_a_missing_key() -> None:
    with pytest.raises(AssistantNotConfiguredError, match="OPENAI_API_KEY"):
        require_client(None)


def test_complete_text_sends_the_request_and_strips_the_reply() -> None:
    fake = FakeOpenAI()
    fake.responses.script("  Hello.  ")

    assert complete(fake) == "Hello."
    assert fake.responses.calls == [
        {"model": "test-model", "instructions": "Be brief.", "input": MESSAGES}
    ]


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (openai.APIConnectionError(request=REQUEST), "APIConnectionError"),
        (openai.APITimeoutError(request=REQUEST), "APITimeoutError"),
        (status_error(openai.InternalServerError, 500), "InternalServerError"),
        (status_error(openai.RateLimitError, 429), "RateLimitError"),
        (status_error(openai.AuthenticationError, 401), "rejected the API key"),
        (status_error(openai.NotFoundError, 404), "OPENAI_MODEL"),
    ],
)
def test_openai_errors_become_unavailable(error: Exception, message: str) -> None:
    fake = FakeOpenAI()
    fake.responses.script(error)

    with pytest.raises(AssistantUnavailableError, match=message) as raised:
        complete(fake)

    assert raised.value.__cause__ is error
    assert "sk-" not in str(raised.value)


def test_an_empty_reply_is_unavailable() -> None:
    fake = FakeOpenAI()
    fake.responses.script("   ")

    with pytest.raises(AssistantUnavailableError, match="empty"):
        complete(fake)


def test_final_message_text_reads_only_the_last_message() -> None:
    def message(text: str) -> SimpleNamespace:
        return SimpleNamespace(
            type="message", content=[SimpleNamespace(type="output_text", text=text)]
        )

    response = SimpleNamespace(
        output=[
            message("Draft."),
            SimpleNamespace(type="reasoning", content=[]),
            message(" Final. "),
        ]
    )

    assert final_message_text(response) == "Final."


def test_final_message_text_without_a_message_is_empty() -> None:
    assert final_message_text(SimpleNamespace(output=[])) == ""
