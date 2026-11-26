"""The single agent: a manual tool-calling loop on the OpenAI Responses API.

Each round sends the conversation so far, with the tool schemas. If the model
asks for function calls, each is run by the toolkit and its output is appended
as a `function_call_output` item for the next round; the first round without
function calls ends the run with the model's text. A model that keeps calling
tools for `max_rounds` rounds fails the request with `AgentStepLimitError`.

Unknown tools and malformed arguments are the toolkit's concern: they come
back as error results the model can react to, and are marked `is_error` in the
trace.
"""

import json
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import openai

from ai_personal_knowledge_assistant.agent.prompts import agent_instructions
from ai_personal_knowledge_assistant.agent.tools import AgentToolkit, ToolResult
from ai_personal_knowledge_assistant.core.errors import AgentStepLimitError
from ai_personal_knowledge_assistant.rag.answerer import ChatTurn, Source
from ai_personal_knowledge_assistant.rag.llm_client import (
    create_response,
    require_client,
    require_reply_text,
)
from ai_personal_knowledge_assistant.storage.records import MessageRole

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ToolCall:
    """One tool call the agent made, for the trace shown to the user."""

    tool: str
    # The parsed JSON arguments, or the raw text when the model sent invalid JSON.
    arguments: Any
    result: Any
    is_error: bool
    summary: str


@dataclass(frozen=True, slots=True)
class AgentRun:
    output: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    # The excerpts behind every answer_from_documents call, without repeats.
    # Each source's `citation` is the [n] of the tool answer it came from.
    sources: list[Source] = field(default_factory=list)


class SingleAgent:
    def __init__(
        self,
        client: openai.OpenAI | None,
        model: str,
        toolkit: AgentToolkit,
        max_rounds: int,
        *,
        parallel_tool_calls: bool = True,
        today: Callable[[], date] = date.today,
    ) -> None:
        """`parallel_tool_calls` lets the model ask for several calls in one
        round; the tools only read, so no call can depend on another's side
        effect, and it saves rounds. `today` is injectable for tests."""
        if max_rounds < 1:
            raise ValueError(f"max_rounds must be at least 1, got {max_rounds}")
        self._client = client
        self._model = model
        self._toolkit = toolkit
        self._max_rounds = max_rounds
        self._parallel_tool_calls = parallel_tool_calls
        self._today = today

    def run(self, message: str, history: Sequence[ChatTurn] = ()) -> AgentRun:
        """Answer `message`, given the earlier conversation (oldest first).

        Raises `AssistantNotConfiguredError` without a key,
        `AssistantUnavailableError` if a model call fails or returns no text,
        and `AgentStepLimitError` after `max_rounds` rounds of tool calls.
        """
        client = require_client(self._client)
        instructions = agent_instructions(self._today())
        items: list[Any] = [
            *({"role": turn.role.value, "content": turn.content} for turn in history),
            {"role": MessageRole.USER.value, "content": message},
        ]
        tool_calls: list[ToolCall] = []
        sources: dict[tuple[int, int], Source] = {}

        for _ in range(self._max_rounds):
            response = create_response(
                client,
                self._model,
                instructions,
                items,
                tools=self._toolkit.definitions,
                parallel_tool_calls=self._parallel_tool_calls,
            )
            calls = [item for item in response.output if item.type == "function_call"]
            if not calls:
                return AgentRun(
                    output=require_reply_text(response, self._model),
                    tool_calls=tool_calls,
                    sources=list(sources.values()),
                )

            # Every output item (reasoning included) goes back so the model
            # keeps its context; each function call is answered by call_id.
            items.extend(response.output)
            for call in calls:
                result = self._toolkit.run(call.name, call.arguments)
                tool_calls.append(_trace(call.name, call.arguments, result))
                for source in result.sources:
                    sources.setdefault((source.document_id, source.chunk_index), source)
                items.append(
                    {
                        "type": "function_call_output",
                        "call_id": call.call_id,
                        "output": result.to_json(),
                    }
                )

        logger.warning(
            "Agent stopped after %d rounds of tool calls: %s",
            self._max_rounds,
            [call.tool for call in tool_calls],
        )
        raise AgentStepLimitError(self._max_rounds)


def _trace(name: str, raw_arguments: str, result: ToolResult) -> ToolCall:
    return ToolCall(
        tool=name,
        arguments=_parse_arguments(raw_arguments),
        result=result.value,
        is_error=result.is_error,
        summary=result.summary,
    )


def _parse_arguments(raw_arguments: str) -> Any:
    try:
        return json.loads(raw_arguments or "{}")
    except json.JSONDecodeError:
        return raw_arguments
