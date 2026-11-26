"""Deterministic stand-ins for the OpenAI SDK clients.

`FakeChatClient` replays scripted Chat Completions responses (built with the
SDK's own types, so the agent parses them exactly like real ones) and records
every request. `FakeEmbeddingClient` embeds text as a hashed bag of words:
texts that share words are similar, which is all retrieval tests need.
"""

import copy
import hashlib
import json
import math
import re
from collections import deque
from types import SimpleNamespace
from typing import Any

import httpx
import openai
from openai.types.chat import ChatCompletion

EMBEDDING_DIMENSIONS = 256
_WORD = re.compile(r"[a-z0-9]+")


def text_completion(content: str) -> ChatCompletion:
    return _completion({"role": "assistant", "content": content}, "stop")


def tool_completion(*calls: tuple[str, dict[str, Any]]) -> ChatCompletion:
    """A reply that calls each `(tool name, arguments)` in order."""
    tool_calls = [
        {
            "id": f"call_{index}",
            "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments)},
        }
        for index, (name, arguments) in enumerate(calls)
    ]
    return _completion(
        {"role": "assistant", "content": None, "tool_calls": tool_calls},
        "tool_calls",
    )


def openai_timeout() -> openai.APITimeoutError:
    return openai.APITimeoutError(
        request=httpx.Request("POST", "https://api.example/v1/chat/completions")
    )


def _completion(message: dict[str, Any], finish_reason: str) -> ChatCompletion:
    return ChatCompletion.model_validate(
        {
            "id": "chatcmpl-fake",
            "object": "chat.completion",
            "created": 0,
            "model": "fake-model",
            "choices": [
                {"index": 0, "finish_reason": finish_reason, "message": message}
            ],
        }
    )


class _FakeCompletions:
    def __init__(self) -> None:
        self.script: deque[ChatCompletion | Exception] = deque()
        self.calls: list[dict[str, Any]] = []

    def create(self, **options: Any) -> ChatCompletion:
        # A snapshot: the caller keeps appending to the same messages list.
        self.calls.append(copy.deepcopy(options))
        if not self.script:
            raise AssertionError("FakeChatClient got an unscripted request")
        reply = self.script.popleft()
        if isinstance(reply, Exception):
            raise reply
        return reply


class FakeChatClient:
    """Duck-types `openai.OpenAI` for `chat.completions.create`."""

    def __init__(self) -> None:
        self._completions = _FakeCompletions()
        self.chat = SimpleNamespace(completions=self._completions)

    def script(self, *replies: ChatCompletion | Exception) -> None:
        self._completions.script.extend(replies)

    @property
    def calls(self) -> list[dict[str, Any]]:
        return self._completions.calls

    @property
    def unused_replies(self) -> int:
        return len(self._completions.script)


class _FakeEmbeddings:
    def __init__(self) -> None:
        self.calls = 0
        self.failure: Exception | None = None

    def create(self, *, model: str, input: list[str]) -> SimpleNamespace:
        self.calls += 1
        if self.failure is not None:
            raise self.failure
        return SimpleNamespace(
            data=[
                SimpleNamespace(index=index, embedding=embed_words(text))
                for index, text in enumerate(input)
            ]
        )


class FakeEmbeddingClient:
    """Duck-types `openai.OpenAI` for `embeddings.create`."""

    def __init__(self) -> None:
        self.embeddings = _FakeEmbeddings()


def embed_words(text: str) -> list[float]:
    vector = [0.0] * EMBEDDING_DIMENSIONS
    for word in _WORD.findall(text.lower()):
        digest = hashlib.blake2b(word.encode(), digest_size=4).digest()
        vector[int.from_bytes(digest) % EMBEDDING_DIMENSIONS] += 1.0
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]
