"""The boundary to the language-model and embedding APIs.

Uses the OpenAI SDK against any OpenAI-compatible endpoint: OpenAI itself by
default, or another provider via OPENAI_BASE_URL / EMBEDDING_BASE_URL (Ollama,
Groq, OpenRouter, Azure-compatible gateways, ...). Only the Chat Completions
and Embeddings APIs are used, because they are the ones those providers share.

Clients are None when no key (and no base URL) is configured, so the shop runs
without AI; whoever needs a model calls `require_client`, which raises
`AssistantNotConfiguredError` at that moment, never at startup. Every
`openai.OpenAIError` becomes `AssistantUnavailableError`.
"""

import logging
from typing import Any

import openai
from openai.types.chat import ChatCompletion

from ai_restaurant_ordering_assistant.config.settings import Settings
from ai_restaurant_ordering_assistant.exceptions.errors import (
    AssistantNotConfiguredError,
    AssistantUnavailableError,
)

# Long enough for a slow tool-calling turn, short enough not to hang a request.
REQUEST_TIMEOUT_SECONDS = 45.0
# The SDK retries connection errors, 408/409/429 and 5xx with backoff.
MAX_RETRIES = 1
# Local servers such as Ollama accept any key but the SDK insists on one.
_PLACEHOLDER_KEY = "not-needed"

logger = logging.getLogger(__name__)


def build_chat_client(settings: Settings) -> openai.OpenAI | None:
    return _build_client(settings.openai_api_key, settings.openai_base_url)


def build_embedding_client(settings: Settings) -> openai.OpenAI | None:
    return _build_client(settings.embeddings_api_key, settings.embeddings_base_url)


def require_client(client: openai.OpenAI | None, purpose: str) -> openai.OpenAI:
    if client is None:
        raise AssistantNotConfiguredError(
            f"{purpose} needs a language-model API: set OPENAI_API_KEY (or "
            "OPENAI_BASE_URL for a local or other provider) in .env and restart."
        )
    return client


def create_chat_completion(
    client: openai.OpenAI,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
) -> ChatCompletion:
    """One Chat Completions call; provider failures become our error."""
    options: dict[str, Any] = {"model": model, "messages": messages}
    if tools:
        options["tools"] = tools
        options["tool_choice"] = "auto"
    try:
        return client.chat.completions.create(**options)
    except openai.OpenAIError as error:
        logger.warning("Chat completion with %s failed: %s", model, error)
        raise AssistantUnavailableError(
            "The AI assistant is temporarily unavailable. Please try again."
        ) from error


def create_embeddings(
    client: openai.OpenAI, model: str, texts: list[str]
) -> list[list[float]]:
    try:
        response = client.embeddings.create(model=model, input=texts)
    except openai.OpenAIError as error:
        logger.warning("Embedding with %s failed: %s", model, error)
        raise AssistantUnavailableError(
            "The document search service is temporarily unavailable."
        ) from error
    return [item.embedding for item in sorted(response.data, key=lambda d: d.index)]


def _build_client(api_key: Any, base_url: str | None) -> openai.OpenAI | None:
    if api_key is None and base_url is None:
        return None
    key = api_key.get_secret_value() if api_key is not None else _PLACEHOLDER_KEY
    return openai.OpenAI(
        api_key=key,
        base_url=base_url,
        timeout=REQUEST_TIMEOUT_SECONDS,
        max_retries=MAX_RETRIES,
    )
