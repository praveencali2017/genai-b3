"""Question -> grounded answer with sources.

`GroundedAnswerer.answer` is the single place a question is answered from the
documents: `POST /chat/ask` calls it directly and the agent's
`answer_from_documents` tool (Step 6) calls the same method.

Policy, in order:

1. Retrieve with the current question only (history is not mixed into the
   search). No chunk above MIN_RELEVANCE -> `NOT_FOUND_ANSWER`, without calling
   the model. This needs no API key: "your documents don't cover this" is
   decided by retrieval alone, so it is answered even when OpenAI isn't set up.
2. Otherwise the model must answer from the numbered excerpts; a missing key is
   reported only now (`AssistantNotConfiguredError`).
3. If the model replies with the not-found sentence (or opens with it and cites
   nothing), the answer is ungrounded and carries no sources, even though
   excerpts were retrieved.
4. Otherwise the sources are the excerpts the reply cites as [n]; markers
   outside 1..len(excerpts) are ignored. If the reply cites nothing valid, every
   retrieved excerpt is returned as a source: the answer was still produced
   from exactly those excerpts, and showing them beats showing nothing.
"""

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass

import openai

from ai_personal_knowledge_assistant.rag.llm_client import (
    InputItem,
    complete_text,
    require_client,
)
from ai_personal_knowledge_assistant.rag.prompts import (
    GROUNDED_ANSWER_INSTRUCTIONS,
    NOT_FOUND_ANSWER,
    build_context,
    build_question_message,
)
from ai_personal_knowledge_assistant.rag.retriever import DocumentRetriever
from ai_personal_knowledge_assistant.storage.records import MessageRole
from ai_personal_knowledge_assistant.vectorstore.chroma_store import RetrievedChunk

SNIPPET_MAX_CHARS = 300

# [3], [1][3] (two matches) and [1, 3].
_CITATION = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")
# Wrapping a model might add around the not-found sentence: quotes, emphasis,
# final punctuation.
_EDGE_NOISE = " \t\n.!\"'`*_"

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Source:
    """One excerpt an answer is based on.

    `citation` is the [n] number the answer text uses for this excerpt, so a UI
    can link markers to sources. `snippet` is the excerpt's text with whitespace
    collapsed, cut to at most SNIPPET_MAX_CHARS.
    """

    citation: int
    document_id: int
    filename: str
    page: int | None
    chunk_index: int
    score: float
    snippet: str


@dataclass(frozen=True, slots=True)
class Answer:
    text: str
    sources: list[Source]
    # False when the documents don't answer the question (text is then
    # NOT_FOUND_ANSWER and there are no sources).
    is_grounded: bool


@dataclass(frozen=True, slots=True)
class ChatTurn:
    """One earlier message of the conversation, given to resolve follow-ups."""

    role: MessageRole
    content: str


class GroundedAnswerer:
    def __init__(
        self, retriever: DocumentRetriever, client: openai.OpenAI | None, model: str
    ) -> None:
        self._retriever = retriever
        self._client = client
        self._model = model

    def answer(
        self,
        question: str,
        document_ids: Sequence[int] | None = None,
        history: Sequence[ChatTurn] = (),
    ) -> Answer:
        """Answer `question` from the documents (all, or only `document_ids`).

        `history` is the recent conversation, oldest first. Raises
        `AssistantNotConfiguredError` / `AssistantUnavailableError` only when
        the model is needed, and `ValueError` for a blank question.
        """
        question = question.strip()
        chunks = self._retriever.search(question, document_ids=document_ids)
        if not chunks:
            logger.info("No relevant excerpts; answering not-found without the LLM")
            return _not_found()

        client = require_client(self._client)
        messages: list[InputItem] = [
            *(_input_item(turn) for turn in history),
            {
                "role": MessageRole.USER.value,
                "content": build_question_message(question, build_context(chunks)),
            },
        ]
        reply = complete_text(
            client, self._model, GROUNDED_ANSWER_INSTRUCTIONS, messages
        )

        cited = cited_numbers(reply, excerpt_count=len(chunks))
        if is_not_found_reply(reply) or (not cited and _opens_with_not_found(reply)):
            return _not_found()
        numbers = cited or list(range(1, len(chunks) + 1))
        sources = [_source(number, chunks[number - 1]) for number in numbers]
        return Answer(text=reply, sources=sources, is_grounded=True)


def cited_numbers(reply: str, excerpt_count: int) -> list[int]:
    """The distinct excerpt numbers `reply` cites, ascending.

    Numbers outside 1..excerpt_count can't refer to an excerpt and are dropped.
    """
    numbers = {
        int(number) for group in _CITATION.findall(reply) for number in group.split(",")
    }
    return sorted(n for n in numbers if 1 <= n <= excerpt_count)


def is_not_found_reply(reply: str) -> bool:
    """Whether the reply is the not-found sentence.

    Tolerates case, curly apostrophes, quotes, emphasis, final punctuation,
    extra whitespace and [n] markers.
    """
    return _normalize(reply) == _normalize(NOT_FOUND_ANSWER)


def _opens_with_not_found(reply: str) -> bool:
    """The model sometimes adds a remark after the not-found sentence. Without a
    citation, nothing in the documents backs that remark, so it counts as
    not found too."""
    return _normalize(reply).startswith(_normalize(NOT_FOUND_ANSWER))


def _normalize(text: str) -> str:
    text = _CITATION.sub("", text).replace("\u2019", "'").casefold()
    return " ".join(text.split()).strip(_EDGE_NOISE)


def _not_found() -> Answer:
    return Answer(text=NOT_FOUND_ANSWER, sources=[], is_grounded=False)


def _input_item(turn: ChatTurn) -> InputItem:
    return {"role": turn.role.value, "content": turn.content}


def _source(citation: int, chunk: RetrievedChunk) -> Source:
    return Source(
        citation=citation,
        document_id=chunk.document_id,
        filename=chunk.filename,
        page=chunk.page,
        chunk_index=chunk.chunk_index,
        score=chunk.score,
        snippet=_snippet(chunk.text),
    )


def _snippet(text: str) -> str:
    flat = " ".join(text.split())
    if len(flat) <= SNIPPET_MAX_CHARS:
        return flat
    cut = flat[: SNIPPET_MAX_CHARS - 1]
    # End on a word boundary unless that would throw away most of the snippet.
    last_space = cut.rfind(" ")
    if last_space > SNIPPET_MAX_CHARS // 2:
        cut = cut[:last_space]
    return cut.rstrip() + "…"
