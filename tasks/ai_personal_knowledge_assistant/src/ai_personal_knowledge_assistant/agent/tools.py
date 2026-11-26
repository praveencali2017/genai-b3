"""The agent's three tools: their OpenAI schemas and the code that runs them.

`AgentToolkit.run(name, raw_arguments)` executes one function call from the
model and always returns a `ToolResult`; it never raises for a bad call. What
goes back to the model as `{"error": ...}` versus what fails the request:

- Fed back to the model, so it can fix the call or explain: an unknown tool
  name, arguments that aren't JSON or don't match the tool's model, and domain
  errors about the request itself (`KnowledgeAssistantError` subclasses that
  aren't listed below).
- Propagated, failing the whole request with the API's usual status: the model
  being unconfigured or unreachable (`AssistantError`: 503 / 502) and the
  vector store failing (`VectorStoreError`: 503). These are outages, not
  answers: fed back, the model would narrate them as "the documents don't say",
  and the user would get a 200 instead of a retryable error. Anything
  unexpected (a bug, a database failure) propagates too and becomes a 500.

The toolkit holds only long-lived services and no per-request state, so one
instance serves concurrent requests. The sources of an `answer_from_documents`
call travel back on its `ToolResult`, for the runner to collect.
"""

import json
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Annotated, Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PositiveInt,
    StringConstraints,
    ValidationError,
)
from sqlalchemy.orm import Session, sessionmaker

from ai_personal_knowledge_assistant.core.errors import (
    AssistantError,
    KnowledgeAssistantError,
    VectorStoreError,
)
from ai_personal_knowledge_assistant.rag.answerer import GroundedAnswerer, Source
from ai_personal_knowledge_assistant.rag.retriever import DocumentRetriever
from ai_personal_knowledge_assistant.storage.database import session_scope
from ai_personal_knowledge_assistant.storage.document_repository import (
    DocumentRepository,
)
from ai_personal_knowledge_assistant.storage.records import (
    DocumentRecord,
    DocumentStatus,
)

LIST_DOCUMENTS = "list_documents"
SEARCH_DOCUMENTS = "search_documents"
ANSWER_FROM_DOCUMENTS = "answer_from_documents"

SEARCH_MAX_RESULTS = 10
TOOL_TEXT_MAX_LENGTH = 2000

# Failures that mean "the service is down", not "the call was wrong".
_PROPAGATED_ERRORS = (AssistantError, VectorStoreError)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------- schemas


def object_schema(
    properties: dict[str, Any], required: Sequence[str] = ()
) -> dict[str, Any]:
    """A strict-mode parameter schema.

    Strict mode makes the model send every property and nothing else, so the
    optional properties become nullable, with null meaning "not given".
    """
    return {
        "type": "object",
        "properties": {
            name: spec if name in required else {**spec, "type": [spec["type"], "null"]}
            for name, spec in properties.items()
        },
        "required": list(properties),
        "additionalProperties": False,
    }


def function_tool(
    name: str, description: str, parameters: dict[str, Any]
) -> dict[str, Any]:
    return {
        "type": "function",
        "name": name,
        "description": description,
        "parameters": parameters,
        "strict": True,
    }


_DOCUMENT_IDS_PROPERTY: dict[str, Any] = {
    "document_ids": {
        "type": "array",
        "items": {"type": "integer"},
        "description": "Restrict to these document ids, taken from a "
        "list_documents result; never guess an id. Use null to cover every "
        "document, which is the usual choice.",
    }
}

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    function_tool(
        LIST_DOCUMENTS,
        "List the user's uploaded documents that are ready to search: id, "
        "filename, file type, page count (null for DOCX/TXT), number of indexed "
        "chunks and upload date. Use it for questions about which files exist, "
        "how many there are or when they were uploaded, and to find document "
        "ids. It says nothing about what the documents contain.",
        object_schema({}),
    ),
    function_tool(
        SEARCH_DOCUMENTS,
        "Find the passages of the user's documents most relevant to a query, "
        "best first, each with filename, page (null for DOCX/TXT), relevance "
        "score 0..1 and the passage text. Use it to explore a topic, to locate "
        "where something is mentioned, or to quote passages. Only passages "
        "above the relevance threshold are returned; an empty list means the "
        "documents contain nothing relevant. For a direct factual question, "
        "prefer answer_from_documents.",
        object_schema(
            {
                "query": {
                    "type": "string",
                    "description": "What to look for, as a short self-contained "
                    "phrase or question, e.g. 'casual leave carry forward'.",
                },
                "top_k": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": SEARCH_MAX_RESULTS,
                    "description": f"How many passages to consider, 1 to "
                    f"{SEARCH_MAX_RESULTS}; null for the default.",
                },
                **_DOCUMENT_IDS_PROPERTY,
            },
            required=["query"],
        ),
    ),
    function_tool(
        ANSWER_FROM_DOCUMENTS,
        "Answer a factual question from the user's documents. Returns the "
        "answer (citing passages as [n]), whether the documents support it "
        "(is_grounded), and its sources with filename and page. When "
        "is_grounded is false the documents don't contain the answer. Use it "
        "for questions about what the documents say.",
        object_schema(
            {
                "question": {
                    "type": "string",
                    "description": "The question, rewritten to stand alone: "
                    "resolve pronouns and follow-ups from the conversation, "
                    "e.g. 'Can unused casual leave be carried forward?'.",
                },
                **_DOCUMENT_IDS_PROPERTY,
            },
            required=["question"],
        ),
    ),
]


# ---------------------------------------------------------------- arguments

ToolText = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=1, max_length=TOOL_TEXT_MAX_LENGTH
    ),
]
DocumentIds = Annotated[list[PositiveInt], Field(min_length=1)]


class _Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ListDocumentsArguments(_Arguments):
    pass


class SearchDocumentsArguments(_Arguments):
    query: ToolText
    top_k: Annotated[int, Field(ge=1, le=SEARCH_MAX_RESULTS)] | None = None
    document_ids: DocumentIds | None = None


class AnswerFromDocumentsArguments(_Arguments):
    question: ToolText
    document_ids: DocumentIds | None = None


# ---------------------------------------------------------------- results


@dataclass(frozen=True, slots=True)
class ToolResult:
    """The outcome of one tool call.

    `value` is JSON-serializable: the tool's output, or `{"error": ...}` when
    the call failed. `summary` is a short human-readable description of it for
    the trace shown to the user. `sources` are the excerpts an
    `answer_from_documents` answer is based on; empty for the other tools.
    """

    value: Any
    is_error: bool
    summary: str
    sources: tuple[Source, ...] = ()

    def to_json(self) -> str:
        return json.dumps(self.value, ensure_ascii=False, default=str)

    @classmethod
    def error(cls, message: str, **details: Any) -> "ToolResult":
        return cls({"error": message, **details}, is_error=True, summary=message)


# ---------------------------------------------------------------- toolkit

_Handler = Callable[[dict[str, Any]], ToolResult]


class AgentToolkit:
    """Runs the agent's tools against the application's services."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        retriever: DocumentRetriever,
        answerer: GroundedAnswerer,
    ) -> None:
        self._session_factory = session_factory
        self._retriever = retriever
        self._answerer = answerer
        self._handlers: dict[str, _Handler] = {
            LIST_DOCUMENTS: self._list_documents,
            SEARCH_DOCUMENTS: self._search_documents,
            ANSWER_FROM_DOCUMENTS: self._answer_from_documents,
        }

    @property
    def definitions(self) -> list[dict[str, Any]]:
        """The tool schemas to send to the model."""
        return TOOL_DEFINITIONS

    def run(self, name: str, raw_arguments: str) -> ToolResult:
        """Execute one call; bad calls come back as error results (see module
        docstring for what propagates instead)."""
        result = self._run(name, raw_arguments)
        logger.info("Tool %s -> %s", name, result.summary)
        return result

    def _run(self, name: str, raw_arguments: str) -> ToolResult:
        handler = self._handlers.get(name)
        if handler is None:
            return ToolResult.error(f"Unknown tool {name!r}.")
        try:
            arguments = json.loads(raw_arguments or "{}")
        except json.JSONDecodeError as error:
            return ToolResult.error(f"Arguments are not valid JSON: {error}")
        if not isinstance(arguments, dict):
            return ToolResult.error("Arguments must be a JSON object.")
        try:
            return handler(arguments)
        except ValidationError as error:
            return ToolResult.error(
                "Invalid arguments.",
                details=error.errors(include_url=False, include_context=False),
            )
        except _PROPAGATED_ERRORS:
            raise
        except KnowledgeAssistantError as error:
            return ToolResult.error(str(error))

    def _list_documents(self, arguments: dict[str, Any]) -> ToolResult:
        ListDocumentsArguments.model_validate(arguments)
        with session_scope(self._session_factory) as session:
            documents = DocumentRepository(session).list_documents(DocumentStatus.READY)
        return ToolResult(
            [_document_entry(document) for document in documents],
            is_error=False,
            summary=_count(len(documents), "document"),
        )

    def _search_documents(self, arguments: dict[str, Any]) -> ToolResult:
        parsed = SearchDocumentsArguments.model_validate(arguments)
        chunks = self._retriever.search(
            parsed.query, document_ids=parsed.document_ids, top_k=parsed.top_k
        )
        excerpts = [
            {
                "document_id": chunk.document_id,
                "filename": chunk.filename,
                "page": chunk.page,
                "score": round(chunk.score, 3),
                "text": chunk.text,
            }
            for chunk in chunks
        ]
        summary = (
            _count(len(excerpts), "excerpt") if excerpts else "no relevant excerpts"
        )
        return ToolResult(excerpts, is_error=False, summary=summary)

    def _answer_from_documents(self, arguments: dict[str, Any]) -> ToolResult:
        parsed = AnswerFromDocumentsArguments.model_validate(arguments)
        answer = self._answerer.answer(
            parsed.question, document_ids=parsed.document_ids
        )
        value = {
            "answer": answer.text,
            "is_grounded": answer.is_grounded,
            "sources": [
                {
                    "citation": source.citation,
                    "filename": source.filename,
                    "page": source.page,
                }
                for source in answer.sources
            ],
        }
        summary = (
            f"grounded answer with {_count(len(answer.sources), 'source')}"
            if answer.is_grounded
            else "not found in the documents"
        )
        return ToolResult(
            value, is_error=False, summary=summary, sources=tuple(answer.sources)
        )


def _document_entry(document: DocumentRecord) -> dict[str, Any]:
    return {
        "id": document.id,
        "filename": document.filename,
        "file_type": document.file_type.value,
        "page_count": document.page_count,
        "chunk_count": document.chunk_count,
        "uploaded": document.created_at.date().isoformat(),
    }


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"
