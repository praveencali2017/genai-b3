"""AgentToolkit: the three tools on real data, bad calls, and what propagates."""

import json
from collections.abc import Sequence
from typing import Any, cast

import httpx2
import openai
import pytest
from pydantic import BaseModel
from sqlalchemy.orm import Session, sessionmaker

from ai_personal_knowledge_assistant.agent.tools import (
    ANSWER_FROM_DOCUMENTS,
    LIST_DOCUMENTS,
    SEARCH_DOCUMENTS,
    TOOL_DEFINITIONS,
    AgentToolkit,
    AnswerFromDocumentsArguments,
    ListDocumentsArguments,
    SearchDocumentsArguments,
    ToolResult,
)
from ai_personal_knowledge_assistant.core.errors import (
    AssistantNotConfiguredError,
    AssistantUnavailableError,
    DocumentNotFoundError,
    VectorStoreError,
)
from ai_personal_knowledge_assistant.rag.prompts import NOT_FOUND_ANSWER
from ai_personal_knowledge_assistant.rag.retriever import DocumentRetriever
from ai_personal_knowledge_assistant.storage.database import session_scope
from ai_personal_knowledge_assistant.storage.document_repository import (
    DocumentRepository,
)
from ai_personal_knowledge_assistant.storage.records import DocumentRecord, FileType
from ai_personal_knowledge_assistant.vectorstore.chroma_store import (
    ChromaVectorStore,
    RetrievedChunk,
)
from tests.agent.conftest import make_toolkit
from tests.fakes import FakeOpenAI
from tests.rag.conftest import FAKE_EMBEDDER_MIN_RELEVANCE, make_answerer

LEAVE_QUERY = "casual leave carried forward"
UNRELATED_QUERY = "capital of France"
GROUNDED_REPLY = "Employees get 12 days of casual leave per calendar year [1]."
REQUEST = httpx2.Request("POST", "https://api.openai.com/v1/responses")


def run(toolkit: AgentToolkit, name: str, **arguments: Any) -> ToolResult:
    return toolkit.run(name, json.dumps(arguments))


# --- Schemas ---


ARGUMENT_MODELS: dict[str, type[BaseModel]] = {
    LIST_DOCUMENTS: ListDocumentsArguments,
    SEARCH_DOCUMENTS: SearchDocumentsArguments,
    ANSWER_FROM_DOCUMENTS: AnswerFromDocumentsArguments,
}


def test_exactly_the_three_approved_tools_are_offered(toolkit: AgentToolkit) -> None:
    assert [tool["name"] for tool in toolkit.definitions] == [
        LIST_DOCUMENTS,
        SEARCH_DOCUMENTS,
        ANSWER_FROM_DOCUMENTS,
    ]


@pytest.mark.parametrize("tool", TOOL_DEFINITIONS, ids=lambda tool: tool["name"])
def test_each_tool_has_a_valid_strict_schema(tool: dict[str, Any]) -> None:
    assert tool["type"] == "function"
    assert tool["strict"] is True
    assert len(tool["description"]) > 40
    parameters = tool["parameters"]
    assert parameters["type"] == "object"
    assert parameters["additionalProperties"] is False
    # Strict mode: every property is required; optional ones are nullable.
    assert parameters["required"] == list(parameters["properties"])
    for spec in parameters["properties"].values():
        assert spec["type"]
        assert spec["description"]
        if "array" in spec["type"]:
            assert spec["items"] == {"type": "integer"}


@pytest.mark.parametrize("tool", TOOL_DEFINITIONS, ids=lambda tool: tool["name"])
def test_schema_properties_match_the_argument_model(tool: dict[str, Any]) -> None:
    model = ARGUMENT_MODELS[tool["name"]]

    assert set(tool["parameters"]["properties"]) == set(model.model_fields)
    required_fields = {
        name for name, field in model.model_fields.items() if field.is_required()
    }
    non_nullable = {
        name
        for name, spec in tool["parameters"]["properties"].items()
        if isinstance(spec["type"], str)
    }
    assert non_nullable == required_fields


# --- list_documents ---


def test_list_documents_returns_the_ready_documents(
    toolkit: AgentToolkit,
    hr_policy: DocumentRecord,
    session_factory: sessionmaker[Session],
) -> None:
    with session_scope(session_factory) as session:
        DocumentRepository(session).add(
            filename="still_processing.txt",
            file_type=FileType.TXT,
            size_bytes=10,
            sha256="0" * 64,
        )

    result = run(toolkit, LIST_DOCUMENTS)

    assert result.is_error is False
    assert result.summary == "1 document"
    assert result.value == [
        {
            "id": hr_policy.id,
            "filename": "HR_Policy.pdf",
            "file_type": "pdf",
            "page_count": hr_policy.page_count,
            "chunk_count": hr_policy.chunk_count,
            "uploaded": hr_policy.created_at.date().isoformat(),
        }
    ]
    assert result.sources == ()
    json.loads(result.to_json())


def test_list_documents_with_nothing_uploaded_is_empty(toolkit: AgentToolkit) -> None:
    result = run(toolkit, LIST_DOCUMENTS)

    assert (result.value, result.is_error, result.summary) == ([], False, "0 documents")


# --- search_documents ---


@pytest.mark.usefixtures("hr_policy")
def test_search_returns_relevant_excerpts_best_first(toolkit: AgentToolkit) -> None:
    result = run(toolkit, SEARCH_DOCUMENTS, query=LEAVE_QUERY)

    assert result.is_error is False
    excerpts = result.value
    assert excerpts
    assert result.summary == f"{len(excerpts)} excerpt" + (
        "s" if len(excerpts) > 1 else ""
    )
    assert set(excerpts[0]) == {"document_id", "filename", "page", "score", "text"}
    scores = [excerpt["score"] for excerpt in excerpts]
    assert scores == sorted(scores, reverse=True)
    assert min(scores) >= FAKE_EMBEDDER_MIN_RELEVANCE
    assert excerpts[0]["page"] == 2
    assert "carried forward" in excerpts[0]["text"]


@pytest.mark.usefixtures("hr_policy")
def test_search_for_something_unrelated_is_empty(toolkit: AgentToolkit) -> None:
    result = run(toolkit, SEARCH_DOCUMENTS, query=UNRELATED_QUERY)

    assert (result.value, result.is_error) == ([], False)
    assert result.summary == "no relevant excerpts"


@pytest.mark.usefixtures("hr_policy")
def test_search_honours_top_k(toolkit: AgentToolkit) -> None:
    result = run(toolkit, SEARCH_DOCUMENTS, query=LEAVE_QUERY, top_k=1)

    assert len(result.value) == 1
    assert result.summary == "1 excerpt"


def test_search_honours_document_ids(
    toolkit: AgentToolkit, hr_policy: DocumentRecord
) -> None:
    other = run(
        toolkit, SEARCH_DOCUMENTS, query=LEAVE_QUERY, document_ids=[hr_policy.id + 1]
    )
    own = run(toolkit, SEARCH_DOCUMENTS, query=LEAVE_QUERY, document_ids=[hr_policy.id])

    assert other.value == []
    assert {excerpt["document_id"] for excerpt in own.value} == {hr_policy.id}


@pytest.mark.usefixtures("hr_policy")
def test_strict_mode_nulls_mean_defaults(toolkit: AgentToolkit) -> None:
    result = run(
        toolkit, SEARCH_DOCUMENTS, query=LEAVE_QUERY, top_k=None, document_ids=None
    )

    assert result.is_error is False
    assert result.value


# --- answer_from_documents ---


@pytest.mark.usefixtures("hr_policy")
def test_answer_returns_the_grounded_answer_and_its_sources(
    toolkit: AgentToolkit, fake_openai: FakeOpenAI
) -> None:
    fake_openai.responses.script(GROUNDED_REPLY)

    result = run(
        toolkit, ANSWER_FROM_DOCUMENTS, question="How many casual leaves are allowed?"
    )

    assert result.is_error is False
    assert result.value == {
        "answer": GROUNDED_REPLY,
        "is_grounded": True,
        "sources": [{"citation": 1, "filename": "HR_Policy.pdf", "page": 2}],
    }
    assert result.summary == "grounded answer with 1 source"
    (source,) = result.sources
    assert (source.filename, source.page, source.citation) == ("HR_Policy.pdf", 2, 1)


@pytest.mark.usefixtures("hr_policy")
def test_answer_to_an_unrelated_question_is_not_found(
    toolkit: AgentToolkit, fake_openai: FakeOpenAI
) -> None:
    result = run(toolkit, ANSWER_FROM_DOCUMENTS, question=UNRELATED_QUERY)

    assert result.value == {
        "answer": NOT_FOUND_ANSWER,
        "is_grounded": False,
        "sources": [],
    }
    assert result.summary == "not found in the documents"
    assert result.sources == ()
    assert fake_openai.responses.calls == []


# --- Bad calls become error results ---


@pytest.mark.parametrize(
    ("name", "raw_arguments", "message"),
    [
        ("delete_documents", "{}", "Unknown tool 'delete_documents'."),
        (SEARCH_DOCUMENTS, '{"query": ', "not valid JSON"),
        (SEARCH_DOCUMENTS, '["casual leave"]', "must be a JSON object"),
        (SEARCH_DOCUMENTS, "{}", "Invalid arguments."),
        (SEARCH_DOCUMENTS, '{"query": "   "}', "Invalid arguments."),
        (SEARCH_DOCUMENTS, '{"query": "leave", "top_k": 11}', "Invalid arguments."),
        (SEARCH_DOCUMENTS, '{"query": "leave", "top_k": 0}', "Invalid arguments."),
        (
            SEARCH_DOCUMENTS,
            '{"query": "leave", "document_ids": []}',
            "Invalid arguments.",
        ),
        (
            ANSWER_FROM_DOCUMENTS,
            '{"question": "leave", "document_ids": [0]}',
            "Invalid arguments.",
        ),
        (LIST_DOCUMENTS, '{"status": "failed"}', "Invalid arguments."),
    ],
    ids=[
        "unknown-tool",
        "malformed-json",
        "not-an-object",
        "missing-query",
        "blank-query",
        "top-k-too-big",
        "top-k-zero",
        "empty-document-ids",
        "bad-document-id",
        "unexpected-argument",
    ],
)
def test_a_bad_call_is_an_error_result_not_an_exception(
    toolkit: AgentToolkit,
    fake_openai: FakeOpenAI,
    name: str,
    raw_arguments: str,
    message: str,
) -> None:
    result = toolkit.run(name, raw_arguments)

    assert result.is_error is True
    assert message in result.value["error"]
    assert result.summary == result.value["error"]
    assert json.loads(result.to_json())["error"] == result.value["error"]
    assert fake_openai.responses.calls == []


def test_validation_errors_tell_the_model_which_field(toolkit: AgentToolkit) -> None:
    result = toolkit.run(SEARCH_DOCUMENTS, '{"query": "leave", "top_k": 11}')

    (detail,) = result.value["details"]
    assert detail["loc"] == ("top_k",)
    json.loads(result.to_json())


def test_empty_arguments_mean_no_arguments(toolkit: AgentToolkit) -> None:
    assert toolkit.run(LIST_DOCUMENTS, "").is_error is False


class FailingRetriever:
    """A retriever whose search raises the given error."""

    def __init__(self, error: Exception) -> None:
        self._error = error

    def search(
        self,
        question: str,
        document_ids: Sequence[int] | None = None,
        top_k: int | None = None,
    ) -> list[RetrievedChunk]:
        raise self._error


def toolkit_with_failing_retriever(
    session_factory: sessionmaker[Session],
    vector_store: ChromaVectorStore,
    error: Exception,
) -> AgentToolkit:
    return AgentToolkit(
        session_factory,
        cast(DocumentRetriever, FailingRetriever(error)),
        make_answerer(vector_store, None),
    )


def test_a_domain_error_is_fed_back(
    session_factory: sessionmaker[Session], vector_store: ChromaVectorStore
) -> None:
    toolkit = toolkit_with_failing_retriever(
        session_factory, vector_store, DocumentNotFoundError(9)
    )

    result = run(toolkit, SEARCH_DOCUMENTS, query=LEAVE_QUERY)

    assert result.value == {"error": "Document 9 not found."}


# --- Outages propagate ---


def test_a_vector_store_failure_propagates(
    session_factory: sessionmaker[Session], vector_store: ChromaVectorStore
) -> None:
    error = VectorStoreError("search the document chunks", RuntimeError("down"))
    toolkit = toolkit_with_failing_retriever(session_factory, vector_store, error)

    with pytest.raises(VectorStoreError):
        run(toolkit, SEARCH_DOCUMENTS, query=LEAVE_QUERY)


@pytest.mark.usefixtures("hr_policy")
def test_a_missing_key_inside_answer_propagates(
    session_factory: sessionmaker[Session], vector_store: ChromaVectorStore
) -> None:
    toolkit = make_toolkit(session_factory, vector_store, None)

    with pytest.raises(AssistantNotConfiguredError):
        run(toolkit, ANSWER_FROM_DOCUMENTS, question="How many casual leaves?")


@pytest.mark.usefixtures("hr_policy")
def test_an_openai_failure_inside_answer_propagates(
    toolkit: AgentToolkit, fake_openai: FakeOpenAI
) -> None:
    fake_openai.responses.script(openai.APIConnectionError(request=REQUEST))

    with pytest.raises(AssistantUnavailableError):
        run(toolkit, ANSWER_FROM_DOCUMENTS, question="How many casual leaves?")
