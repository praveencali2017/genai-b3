"""Test doubles for the application's external boundaries."""

import hashlib
import json
import math
import re
from collections import deque
from collections.abc import Sequence
from types import SimpleNamespace
from typing import Any

from ai_personal_knowledge_assistant.core.errors import VectorStoreError
from ai_personal_knowledge_assistant.ingestion.chunker import Chunk
from ai_personal_knowledge_assistant.vectorstore.chroma_store import ChromaVectorStore

_TOKEN = re.compile(r"[a-z0-9]+")

# Function words carry no topic, so they would make unrelated texts look similar.
_STOPWORDS = frozenset(
    "a an and are as at be by can do does for from has have how i in is it many "
    "much my of on or the to was we what when where which who why will with you "
    "your".split()
)


class FakeEmbedder:
    """Deterministic bag-of-words embedder; no model, no network.

    Each lower-cased word (stopwords dropped) is hashed with BLAKE2 into one of
    `dimension - 1` buckets; the counts are L2-normalized. The cosine similarity
    of two texts is therefore their word-overlap: texts sharing topic words score
    high, texts with no words in common score 0, and identical word bags score 1.
    BLAKE2 (unlike `hash()`) is unsalted, so vectors are stable across runs; a
    hash collision between two words is possible but reproducible.

    A text with no topic words maps to the reserved last dimension, so every
    vector has unit length (Chroma can't compute cosine distance for a zero
    vector) and such texts match only each other.

    `document_batch_sizes` records the size of every `embed_documents` call.
    """

    def __init__(self, dimension: int = 512) -> None:
        if dimension < 2:
            raise ValueError("dimension must be at least 2")
        self._dimension = dimension
        self.document_batch_sizes: list[int] = []

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        self.document_batch_sizes.append(len(texts))
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self._dimension
        words = [w for w in _TOKEN.findall(text.lower()) if w not in _STOPWORDS]
        if not words:
            vector[-1] = 1.0
            return vector
        for word in words:
            vector[self._bucket(word)] += 1.0
        norm = math.sqrt(sum(value * value for value in vector))
        return [value / norm for value in vector]

    def _bucket(self, word: str) -> int:
        digest = hashlib.blake2b(word.encode(), digest_size=8).digest()
        return int.from_bytes(digest) % (self._dimension - 1)


class FlakyVectorStore(ChromaVectorStore):
    """A real Chroma store with switchable faults, for testing failure cleanup.

    With `fail_adds` set, `add_chunks` writes the first chunk and then raises,
    like a write that dies halfway. With `fail_deletes` set, `delete_document`
    raises without deleting anything.
    """

    fail_adds = False
    fail_deletes = False

    def add_chunks(
        self, document_id: int, filename: str, chunks: Sequence[Chunk]
    ) -> int:
        if not self.fail_adds:
            return super().add_chunks(document_id, filename, chunks)
        super().add_chunks(document_id, filename, chunks[:1])
        raise VectorStoreError(
            f"store the chunks of document {document_id}",
            RuntimeError("injected add fault"),
        )

    def delete_document(self, document_id: int) -> None:
        if self.fail_deletes:
            raise VectorStoreError(
                f"delete the chunks of document {document_id}",
                RuntimeError("injected delete fault"),
            )
        super().delete_document(document_id)


class FakePageOcr:
    """`PageOcr` that returns scripted text per page and records what it read.

    Pages missing from `texts_by_page` read as "". With `error` set, every read
    raises it instead.
    """

    def __init__(
        self,
        texts_by_page: dict[int, str] | None = None,
        error: Exception | None = None,
    ) -> None:
        self._texts_by_page = texts_by_page or {}
        self._error = error
        self.pages_read: list[int] = []

    def read_page(self, pdf: bytes, page_number: int) -> str:
        assert pdf.startswith(b"%PDF-"), "OCR must be given the PDF's bytes"
        self.pages_read.append(page_number)
        if self._error is not None:
            raise self._error
        return self._texts_by_page.get(page_number, "")


# A scripted model reply: text, a prebuilt Responses API result (e.g. from
# `tool_calls_response`), or an exception to raise.
ScriptedReply = str | SimpleNamespace | Exception


class FakeResponses:
    """Stands in for `client.responses`: replays scripted replies, records calls.

    Each `create` call is recorded (its keyword arguments) in `calls` and takes
    the next scripted reply: a string becomes a one-message Responses API
    result, a `SimpleNamespace` is returned as the result, an exception is
    raised. Calling with nothing scripted fails the test, so an unexpected
    model call can't pass silently.

    `input` is recorded as a copy: the real client serializes it during the
    call, so a caller that keeps appending to its list afterwards (the agent
    loop does) must not change what an earlier call appears to have sent.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self._replies: deque[ScriptedReply] = deque()

    def script(self, *replies: ScriptedReply) -> None:
        self._replies.extend(replies)

    def create(self, **kwargs: Any) -> SimpleNamespace:
        if "input" in kwargs:
            kwargs = {**kwargs, "input": list(kwargs["input"])}
        self.calls.append(kwargs)
        if not self._replies:
            raise AssertionError(f"unexpected OpenAI call: {kwargs}")
        reply = self._replies.popleft()
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, str):
            return text_response(reply)
        return reply


class FakeOpenAI:
    """Stands in for `openai.OpenAI`; only `responses.create` is used."""

    def __init__(self) -> None:
        self.responses = FakeResponses()


def text_response(text: str) -> SimpleNamespace:
    """A Responses API result holding one assistant message with `text`."""
    message = SimpleNamespace(
        type="message", content=[SimpleNamespace(type="output_text", text=text)]
    )
    return SimpleNamespace(id="resp_fake", output=[message], output_text=text)


def function_call(
    name: str, arguments: dict[str, Any] | str, call_id: str | None = None
) -> SimpleNamespace:
    """A Responses API function-call output item.

    A dict is sent as JSON, a string as-is (to script malformed arguments).
    """
    return SimpleNamespace(
        type="function_call",
        name=name,
        arguments=arguments if isinstance(arguments, str) else json.dumps(arguments),
        call_id=call_id or f"call_{name}",
    )


def tool_calls_response(*calls: SimpleNamespace) -> SimpleNamespace:
    """A Responses API result in which the model asks for `calls`."""
    return SimpleNamespace(id="resp_fake_tools", output=list(calls), output_text="")
