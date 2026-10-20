"""Chat model adapters: one per LLM provider, all behind the ChatModel protocol.

These are the only code that knows a provider's request and response shapes.
ChatService depends on ChatModel alone, so the provider can be swapped at
runtime without touching the chat logic.
"""

from typing import Protocol

import openai


class ChatProviderError(RuntimeError):
    """The provider call failed or returned something unusable."""


class ChatModel(Protocol):
    """Strategy interface: turn instructions and a prompt into the model's raw text."""

    @property
    def model(self) -> str: ...

    def complete(self, instructions: str, prompt: str) -> str: ...


class OpenAIResponsesModel:
    """OpenAI through the Responses API."""

    def __init__(self, client: openai.OpenAI, model: str) -> None:
        self._client = client
        self._model = model

    @property
    def model(self) -> str:
        return self._model

    def complete(self, instructions: str, prompt: str) -> str:
        try:
            response = self._client.responses.create(
                model=self._model, instructions=instructions, input=prompt
            )
        except openai.OpenAIError as error:
            raise ChatProviderError(f"OpenAI request failed (model={self._model})") from error
        return response.output_text


class OllamaChatModel:
    """A local Ollama model through its OpenAI-compatible Chat Completions API."""

    def __init__(self, client: openai.OpenAI, model: str) -> None:
        self._client = client
        self._model = model

    @property
    def model(self) -> str:
        return self._model

    def complete(self, instructions: str, prompt: str) -> str:
        try:
            completion = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": instructions},
                    {"role": "user", "content": prompt},
                ],
            )
        except openai.OpenAIError as error:
            raise ChatProviderError(f"Ollama request failed (model={self._model})") from error
        if not completion.choices:
            return ""
        return completion.choices[0].message.content or ""
