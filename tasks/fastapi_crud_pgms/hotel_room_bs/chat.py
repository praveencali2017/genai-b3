"""Answer chat prompts with the OpenAI Responses API. All OpenAI calls live here."""

import logging
import os
from dataclasses import dataclass
from functools import cache

import openai
from dotenv import load_dotenv

from db_config import find_env_file

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gpt-5.4-mini"
REQUEST_TIMEOUT_SECONDS = 30.0
INSTRUCTIONS = (
    "You are a friendly assistant inside a Hotel Room Booking web app. "
    "Answer the user's question simply and briefly, in plain text, "
    "in at most a few sentences."
)


class ChatNotConfiguredError(RuntimeError):
    """OPENAI_API_KEY is not set, so the chat can't call OpenAI."""


class ChatUnavailableError(RuntimeError):
    """OpenAI failed or returned no answer; the cause is logged."""


@dataclass(frozen=True)
class ChatSettings:
    api_key: str
    model: str


def load_chat_settings() -> ChatSettings:
    """Read OPENAI_API_KEY and the optional OPENAI_MODEL from the environment or .env.

    The key is optional for the app as a whole: without it the student API
    still works and only /chat reports that it isn't configured.
    """
    env_file = find_env_file()
    if env_file is not None:
        load_dotenv(env_file)

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise ChatNotConfiguredError(
            "The chat assistant isn't configured. Set OPENAI_API_KEY in .env."
        )
    model = os.getenv("OPENAI_MODEL", "").strip() or DEFAULT_MODEL
    return ChatSettings(api_key=api_key, model=model)


class ChatService:
    """Turns one prompt into one short answer. No conversation history is kept."""

    def __init__(self, client: openai.OpenAI, model: str) -> None:
        self._client = client
        self._model = model

    def answer(self, prompt: str) -> str:
        try:
            response = self._client.responses.create(
                model=self._model, instructions=INSTRUCTIONS, input=prompt
            )
        except openai.OpenAIError as error:
            logger.exception("OpenAI request failed (model=%s)", self._model)
            raise ChatUnavailableError(
                "The assistant couldn't answer right now. Please try again."
            ) from error

        answer = response.output_text.strip()
        if not answer:
            logger.error("OpenAI returned no text (response id=%s)", response.id)
            raise ChatUnavailableError("The assistant returned an empty answer.")
        return answer


@cache
def _openai_client(api_key: str) -> openai.OpenAI:
    # One client per key: it pools connections and is safe to share across threads.
    return openai.OpenAI(
        api_key=api_key, timeout=REQUEST_TIMEOUT_SECONDS, max_retries=1
    )


def get_chat_service() -> ChatService:
    """FastAPI dependency: a ChatService on the shared OpenAI client."""
    settings = load_chat_settings()
    return ChatService(_openai_client(settings.api_key), settings.model)
