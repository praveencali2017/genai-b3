"""SingleAgent: the tool-calling loop against a scripted model and real tools."""

import json
from datetime import date
from typing import Any, cast

import httpx2
import openai
import pytest

from ai_personal_knowledge_assistant.agent.runner import SingleAgent, ToolCall
from ai_personal_knowledge_assistant.agent.tools import (
    ANSWER_FROM_DOCUMENTS,
    LIST_DOCUMENTS,
    SEARCH_DOCUMENTS,
    TOOL_DEFINITIONS,
    AgentToolkit,
)
from ai_personal_knowledge_assistant.core.errors import (
    AgentStepLimitError,
    AssistantNotConfiguredError,
    AssistantUnavailableError,
)
from ai_personal_knowledge_assistant.rag.answerer import ChatTurn
from ai_personal_knowledge_assistant.storage.records import DocumentRecord, MessageRole
from tests.agent.conftest import MAX_ROUNDS, make_agent
from tests.fakes import FakeOpenAI, function_call, tool_calls_response
from tests.rag.conftest import TEST_MODEL

REQUEST = httpx2.Request("POST", "https://api.openai.com/v1/responses")
LIST_REPLY = "You have one document: HR_Policy.pdf."
LEAVE_QUESTION = "How many casual leaves are allowed?"
GROUNDED_REPLY = "Employees get 12 days of casual leave per calendar year [1]."


@pytest.fixture
def agent(fake_openai: FakeOpenAI, toolkit: AgentToolkit) -> SingleAgent:
    return make_agent(fake_openai, toolkit)


def function_call_outputs(call: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        item
        for item in call["input"]
        if isinstance(item, dict) and item.get("type") == "function_call_output"
    ]


# --- Tool use ---


def test_listing_documents_runs_the_tool_and_returns_the_reply(
    agent: SingleAgent, fake_openai: FakeOpenAI, hr_policy: DocumentRecord
) -> None:
    list_call = function_call(LIST_DOCUMENTS, {}, call_id="call_1")
    fake_openai.responses.script(tool_calls_response(list_call), LIST_REPLY)

    run = agent.run("List my uploaded documents.")

    assert run.output == LIST_REPLY
    (tool_call,) = run.tool_calls
    assert (tool_call.tool, tool_call.arguments, tool_call.is_error) == (
        LIST_DOCUMENTS,
        {},
        False,
    )
    assert tool_call.summary == "1 document"
    assert tool_call.result[0]["filename"] == "HR_Policy.pdf"
    assert run.sources == []

    first, second = fake_openai.responses.calls
    assert first["input"] == [
        {"role": "user", "content": "List my uploaded documents."}
    ]
    # The second round carries the model's call and the tool's output for it.
    assert list_call in second["input"]
    (output,) = function_call_outputs(second)
    assert output["call_id"] == "call_1"
    assert json.loads(output["output"])[0]["id"] == hr_policy.id
    assert "HR_Policy.pdf" in output["output"]


def test_each_round_sends_the_model_tools_and_instructions(
    fake_openai: FakeOpenAI, toolkit: AgentToolkit
) -> None:
    agent = SingleAgent(
        cast(openai.OpenAI, fake_openai),
        TEST_MODEL,
        toolkit,
        MAX_ROUNDS,
        today=lambda: date(2026, 11, 24),
    )
    fake_openai.responses.script("Hello! Ask me about your documents.")

    agent.run("Hi")

    (call,) = fake_openai.responses.calls
    assert call["model"] == TEST_MODEL
    assert call["tools"] == TOOL_DEFINITIONS
    assert call["parallel_tool_calls"] is True
    assert "Today is 2026-11-24." in call["instructions"]
    for tool in (LIST_DOCUMENTS, SEARCH_DOCUMENTS, ANSWER_FROM_DOCUMENTS):
        assert tool in call["instructions"]


@pytest.mark.usefixtures("hr_policy")
def test_a_leave_question_is_answered_with_sources(
    agent: SingleAgent, fake_openai: FakeOpenAI
) -> None:
    fake_openai.responses.script(
        tool_calls_response(
            function_call(ANSWER_FROM_DOCUMENTS, {"question": LEAVE_QUESTION})
        ),
        GROUNDED_REPLY,  # the answerer's own model call
        "You get 12 casual leave days a year (HR_Policy.pdf, page 2).",
    )

    run = agent.run(LEAVE_QUESTION)

    assert run.output == "You get 12 casual leave days a year (HR_Policy.pdf, page 2)."
    (tool_call,) = run.tool_calls
    assert tool_call.tool == ANSWER_FROM_DOCUMENTS
    assert tool_call.summary == "grounded answer with 1 source"
    (source,) = run.sources
    assert (source.filename, source.page) == ("HR_Policy.pdf", 2)
    (output,) = function_call_outputs(fake_openai.responses.calls[2])
    assert json.loads(output["output"])["is_grounded"] is True


@pytest.mark.usefixtures("hr_policy")
def test_parallel_calls_all_run_and_are_answered_in_order(
    agent: SingleAgent, fake_openai: FakeOpenAI
) -> None:
    fake_openai.responses.script(
        tool_calls_response(
            function_call(LIST_DOCUMENTS, {}, call_id="call_a"),
            function_call(
                SEARCH_DOCUMENTS,
                {"query": "casual leave", "top_k": None, "document_ids": None},
                call_id="call_b",
            ),
        ),
        "Done.",
    )

    run = agent.run("What do I have about casual leave?")

    assert [call.tool for call in run.tool_calls] == [LIST_DOCUMENTS, SEARCH_DOCUMENTS]
    outputs = function_call_outputs(fake_openai.responses.calls[1])
    assert [output["call_id"] for output in outputs] == ["call_a", "call_b"]


@pytest.mark.usefixtures("hr_policy")
def test_sources_from_repeated_answers_are_not_duplicated(
    agent: SingleAgent, fake_openai: FakeOpenAI
) -> None:
    ask = function_call(ANSWER_FROM_DOCUMENTS, {"question": LEAVE_QUESTION})
    fake_openai.responses.script(
        tool_calls_response(ask),
        GROUNDED_REPLY,
        tool_calls_response(ask),
        GROUNDED_REPLY,
        "12 days.",
    )

    run = agent.run(LEAVE_QUESTION)

    assert len(run.tool_calls) == 2
    assert len(run.sources) == 1


def test_history_comes_before_the_new_message(
    agent: SingleAgent, fake_openai: FakeOpenAI
) -> None:
    fake_openai.responses.script("It is HR_Policy.pdf.")
    history = [
        ChatTurn(MessageRole.USER, "List my uploaded documents."),
        ChatTurn(MessageRole.ASSISTANT, LIST_REPLY),
    ]

    agent.run("Which one was that?", history)

    assert fake_openai.responses.calls[0]["input"] == [
        {"role": "user", "content": "List my uploaded documents."},
        {"role": "assistant", "content": LIST_REPLY},
        {"role": "user", "content": "Which one was that?"},
    ]


# --- Bad calls are fed back, not raised ---


@pytest.mark.parametrize(
    ("call", "expected_arguments", "message"),
    [
        (function_call("delete_documents", {"id": 1}), {"id": 1}, "Unknown tool"),
        (function_call(SEARCH_DOCUMENTS, '{"query": '), '{"query": ', "valid JSON"),
    ],
    ids=["unknown-tool", "malformed-arguments"],
)
def test_a_bad_call_is_reported_to_the_model_and_the_run_continues(
    agent: SingleAgent,
    fake_openai: FakeOpenAI,
    call: Any,
    expected_arguments: Any,
    message: str,
) -> None:
    fake_openai.responses.script(tool_calls_response(call), "Sorry, I can't do that.")

    run = agent.run("Delete my documents.")

    assert run.output == "Sorry, I can't do that."
    (tool_call,) = run.tool_calls
    assert tool_call == ToolCall(
        tool=call.name,
        arguments=expected_arguments,
        result=tool_call.result,
        is_error=True,
        summary=tool_call.result["error"],
    )
    assert message in tool_call.summary
    (output,) = function_call_outputs(fake_openai.responses.calls[1])
    assert message in json.loads(output["output"])["error"]


# --- Failures ---


def test_a_model_that_never_stops_calling_tools_hits_the_step_limit(
    agent: SingleAgent, fake_openai: FakeOpenAI
) -> None:
    fake_openai.responses.script(
        *(
            tool_calls_response(function_call(LIST_DOCUMENTS, {}, call_id=f"c{n}"))
            for n in range(MAX_ROUNDS)
        )
    )

    with pytest.raises(AgentStepLimitError) as raised:
        agent.run("List my uploaded documents.")

    assert raised.value.max_rounds == MAX_ROUNDS
    assert f"within {MAX_ROUNDS} steps" in str(raised.value)
    assert len(fake_openai.responses.calls) == MAX_ROUNDS


def test_without_a_key_the_model_is_never_called(toolkit: AgentToolkit) -> None:
    with pytest.raises(AssistantNotConfiguredError, match="OPENAI_API_KEY"):
        make_agent(None, toolkit).run("List my uploaded documents.")


def test_an_openai_failure_is_unavailable(
    agent: SingleAgent, fake_openai: FakeOpenAI
) -> None:
    fake_openai.responses.script(openai.APIConnectionError(request=REQUEST))

    with pytest.raises(AssistantUnavailableError, match="APIConnectionError"):
        agent.run("List my uploaded documents.")


def test_an_empty_final_reply_is_unavailable(
    agent: SingleAgent, fake_openai: FakeOpenAI
) -> None:
    fake_openai.responses.script("   ")

    with pytest.raises(AssistantUnavailableError, match="empty"):
        agent.run("Hi")


def test_max_rounds_must_be_positive(toolkit: AgentToolkit) -> None:
    with pytest.raises(ValueError, match="max_rounds"):
        make_agent(FakeOpenAI(), toolkit, max_rounds=0)
