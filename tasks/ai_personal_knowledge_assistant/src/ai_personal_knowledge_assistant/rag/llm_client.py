"""The boundary to OpenAI: building the client and turning its failures into ours.

Two pieces, deliberately thin:

- `build_openai_client` returns None when OPENAI_API_KEY is unset, so the app
  starts (and uploads and listing work) without a key. Whoever needs the model
  calls `require_client`, which raises `AssistantNotConfiguredError` at that
  moment, never at startup.
- `complete_text` makes one plain-text Responses API call and maps every
  `openai.OpenAIError` to `AssistantUnavailableError`.

The raw `openai.OpenAI` client is what the rest of the app holds, rather than a
wrapper class, because the agent (Step 6) calls `client.responses.create` with
tools and reads function-call items; a text-only abstraction would have to be
bypassed there anyway. Tests pass a fake with the same `responses.create`.
"""

import logging
from typing import Any

import openai

from ai_personal_knowledge_assistant.core.errors import (
    AssistantNotConfiguredError,
    AssistantUnavailableError,
)
from ai_personal_knowledge_assistant.core.settings import Settings

# A grounded answer is a short completion; 30 s covers a slow response without
# leaving a request hanging. One retry rides out a transient blip (the SDK
# retries connection errors, 408/409/429 and 5xx with backoff) without doubling
# a user's wait on a real outage.
OPENAI_TIMEOUT_SECONDS = 30.0
OPENAI_MAX_RETRIES = 1

# One Responses API input item: {"role": "user" | "assistant", "content": "..."}.
InputItem = dict[str, Any]

logger = logging.getLogger(__name__)


def build_openai_client(settings: Settings) -> openai.OpenAI | None:
    """A client for the configured key, or None when no key is set.

    Building the client makes no network call.
    """
    if settings.openai_api_key is None:
        return None
    return openai.OpenAI(
        api_key=settings.openai_api_key.get_secret_value(),
        timeout=OPENAI_TIMEOUT_SECONDS,
        max_retries=OPENAI_MAX_RETRIES,
    )


def require_client(client: openai.OpenAI | None) -> openai.OpenAI:
    """The client, or `AssistantNotConfiguredError` if there is none."""
    if client is None:
        raise AssistantNotConfiguredError(
            "Answering questions needs an OpenAI API key: set OPENAI_API_KEY in "
            ".env and restart the API. Uploading and listing documents work "
            "without it."
        )
    return client


def complete_text(
    client: openai.OpenAI,
    model: str,
    instructions: str,
    messages: list[InputItem],
) -> str:
    """The model's text reply to `messages`, stripped.

    Raises `AssistantUnavailableError` if the call fails or the reply is empty.
    """
    try:
        response = client.responses.create(
            model=model, instructions=instructions, input=messages
        )
    except openai.OpenAIError as error:
        logger.exception("OpenAI request failed (model=%s)", model)
        raise AssistantUnavailableError(_describe_failure(error)) from error

    text = final_message_text(response)
    if not text:
        logger.error("OpenAI returned no text (model=%s, id=%s)", model, response.id)
        raise AssistantUnavailableError("The language model returned an empty answer.")
    return text


def final_message_text(response: Any) -> str:
    """Text of the last assistant message in a Responses API result.

    Not `response.output_text`: that joins every message item, and a model
    occasionally emits a draft and a final version, which would repeat the
    answer.
    """
    messages = [item for item in response.output if item.type == "message"]
    if not messages:
        return ""
    parts = [part.text for part in messages[-1].content if part.type == "output_text"]
    return "".join(parts).strip()


def _describe_failure(error: openai.OpenAIError) -> str:
    """A message for the user; the full provider error goes to the log only
    (an authentication error, for instance, echoes part of the key)."""
    if isinstance(error, openai.AuthenticationError | openai.PermissionDeniedError):
        return "OpenAI rejected the API key: check OPENAI_API_KEY in .env."
    if isinstance(error, openai.NotFoundError):
        return "OpenAI doesn't know the configured model: check OPENAI_MODEL in .env."
    return (
        "The language model is unavailable right now "
        f"({type(error).__name__}). Please try again."
    )
