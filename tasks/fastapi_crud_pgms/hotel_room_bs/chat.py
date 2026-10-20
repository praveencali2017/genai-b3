"""Answer chat prompts with OpenAI or a local Ollama model, chosen from the environment.

OLLAMA_ENABLED picks the provider; the chat logic in ChatService is the same
for both and only the ChatModel adapter (see chat_models.py) changes.
"""

import logging
import os
from dataclasses import dataclass
from functools import cache

import openai
from dotenv import load_dotenv

from chat_models import (
    ChatModel,
    ChatProviderError,
    OllamaChatModel,
    OpenAIResponsesModel,
)
from db_config import find_env_file

logger = logging.getLogger(__name__)

DEFAULT_OPENAI_MODEL = "gpt-5.4-mini"
DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434/v1"
DEFAULT_OLLAMA_MODEL = "llama3.2"
# Ollama ignores the key, but the OpenAI SDK refuses to build a client without one.
OLLAMA_PLACEHOLDER_API_KEY = "ollama"
REQUEST_TIMEOUT_SECONDS = 30.0
# A local model can take a while to load into memory on its first request.
OLLAMA_REQUEST_TIMEOUT_SECONDS = 120.0
TRUE_VALUES = frozenset({"true", "1", "yes", "on"})
FALSE_VALUES = frozenset({"false", "0", "no", "off", ""})
INSTRUCTIONS = (
    "You are a friendly assistant inside a Hotel Room Booking web app. "
    "Answer the user's question simply and briefly, in plain text, "
    "in at most a few sentences."
)


class ChatNotConfiguredError(RuntimeError):
    """The chat's environment settings are missing or invalid."""


class ChatUnavailableError(RuntimeError):
    """The model failed or returned no answer; the cause is logged."""


@dataclass(frozen=True)
class OpenAISettings:
    api_key: str
    model: str


@dataclass(frozen=True)
class OllamaSettings:
    base_url: str
    model: str


ChatSettings = OpenAISettings | OllamaSettings


@dataclass(frozen=True)
class ChatAnswer:
    text: str
    model: str


def _read_ollama_enabled() -> bool:
    raw = os.getenv("OLLAMA_ENABLED", "").strip().lower()
    if raw in TRUE_VALUES:
        return True
    if raw in FALSE_VALUES:
        return False
    raise ChatNotConfiguredError(
        f"OLLAMA_ENABLED must be true or false, got {raw!r}. Fix it in .env."
    )


def load_chat_settings() -> ChatSettings:
    """Read the chat provider's settings from the environment or .env.

    OLLAMA_ENABLED=true selects Ollama (OLLAMA_BASE_URL, OLLAMA_MODEL); otherwise
    OpenAI is used and OPENAI_API_KEY is required. The settings are optional for
    the app as a whole: without them the booking API still works and only /chat
    reports that it isn't configured.
    """
    env_file = find_env_file()
    if env_file is not None:
        load_dotenv(env_file)

    if _read_ollama_enabled():
        return OllamaSettings(
            base_url=os.getenv("OLLAMA_BASE_URL", "").strip() or DEFAULT_OLLAMA_BASE_URL,
            model=os.getenv("OLLAMA_MODEL", "").strip() or DEFAULT_OLLAMA_MODEL,
        )

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise ChatNotConfiguredError(
            "The chat assistant isn't configured. Set OPENAI_API_KEY in .env, "
            "or set OLLAMA_ENABLED=true to use a local Ollama model."
        )
    model = os.getenv("OPENAI_MODEL", "").strip() or DEFAULT_OPENAI_MODEL
    return OpenAISettings(api_key=api_key, model=model)


class ChatService:
    """Turns one prompt into one short answer. No conversation history is kept."""

    def __init__(self, chat_model: ChatModel) -> None:
        self._chat_model = chat_model

    def answer(self, prompt: str) -> ChatAnswer:
        model = self._chat_model.model
        try:
            text = self._chat_model.complete(INSTRUCTIONS, prompt).strip()
        except ChatProviderError as error:
            logger.exception("Chat model request failed (model=%s)", model)
            raise ChatUnavailableError(
                "The assistant couldn't answer right now. Please try again."
            ) from error

        if not text:
            logger.error("Chat model returned no text (model=%s)", model)
            raise ChatUnavailableError("The assistant returned an empty answer.")
        return ChatAnswer(text=text, model=model)


# One client per settings value: it pools connections and is safe to share across threads.
@cache
def _openai_client(api_key: str) -> openai.OpenAI:
    return openai.OpenAI(api_key=api_key, timeout=REQUEST_TIMEOUT_SECONDS, max_retries=1)


@cache
def _ollama_client(base_url: str) -> openai.OpenAI:
    return openai.OpenAI(
        base_url=base_url,
        api_key=OLLAMA_PLACEHOLDER_API_KEY,
        timeout=OLLAMA_REQUEST_TIMEOUT_SECONDS,
        max_retries=1,
    )


def build_chat_model(settings: ChatSettings) -> ChatModel:
    """Pick the ChatModel strategy that matches the configured provider."""
    match settings:
        case OllamaSettings(base_url=base_url, model=model):
            return OllamaChatModel(_ollama_client(base_url), model)
        case OpenAISettings(api_key=api_key, model=model):
            return OpenAIResponsesModel(_openai_client(api_key), model)


def get_chat_service() -> ChatService:
    """FastAPI dependency: a ChatService on the provider chosen by the environment."""
    return ChatService(build_chat_model(load_chat_settings()))
