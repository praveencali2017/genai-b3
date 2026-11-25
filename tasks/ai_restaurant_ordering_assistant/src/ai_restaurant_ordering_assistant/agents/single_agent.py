"""The single agent: one Chat Completions tool loop per user message.

The model sees the system prompt, the newest turns of the conversation and the
new message, plus the seven tools. Each round it either calls tools (we run
them and feed back the results) or answers. After `max_iterations` rounds of
tool calls it is asked once more, without tools, to answer with what it has.

The reply's mode is derived from the tools that actually ran: none -> GENERAL,
only retrieve_policy -> RAG, only business tools -> AGENT, both -> MIXED.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import openai

from ai_restaurant_ordering_assistant.agents.tools import (
    BUSINESS_TOOLS,
    POLICY_TOOLS,
    TOOL_DEFINITIONS,
    AgentToolkit,
)
from ai_restaurant_ordering_assistant.llm.openai_client import create_chat_completion
from ai_restaurant_ordering_assistant.models.enums import ChatMode, MessageRole
from ai_restaurant_ordering_assistant.rag.vector_store import ChunkHit

_OUT_OF_ROUNDS_NOTE = (
    "You have used all tool rounds for this message. Answer now with what the "
    "tool results above show, and say what you could not finish."
)
_EMPTY_REPLY = "Sorry, I couldn't come up with an answer. Could you rephrase?"


@dataclass(frozen=True, slots=True)
class HistoryTurn:
    role: MessageRole
    content: str


@dataclass(frozen=True, slots=True)
class ToolCallRecord:
    name: str
    arguments: Any
    result: Any
    is_error: bool


@dataclass(slots=True)
class AgentReply:
    content: str
    mode: ChatMode
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    evidence: list[ChunkHit] = field(default_factory=list)
    pending_action_ids: list[int] = field(default_factory=list)


class SingleAgent:
    def __init__(self, client: openai.OpenAI, model: str, max_iterations: int) -> None:
        self._client = client
        self._model = model
        self._max_iterations = max_iterations

    def reply(
        self,
        system_prompt: str,
        history: Sequence[HistoryTurn],
        user_message: str,
        toolkit: AgentToolkit,
    ) -> AgentReply:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            *({"role": turn.role.value, "content": turn.content} for turn in history),
            {"role": "user", "content": user_message},
        ]
        reply = AgentReply(content="", mode=ChatMode.GENERAL)

        for _ in range(self._max_iterations):
            completion = create_chat_completion(
                self._client, self._model, messages, TOOL_DEFINITIONS
            )
            message = completion.choices[0].message
            if not message.tool_calls:
                reply.content = (message.content or "").strip() or _EMPTY_REPLY
                break
            messages.append(message.model_dump(exclude_none=True))
            for call in message.tool_calls:
                outcome = toolkit.run(call.function.name, call.function.arguments)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": outcome.to_json(),
                    }
                )
                reply.tool_calls.append(
                    ToolCallRecord(
                        name=call.function.name,
                        arguments=_parsed(call.function.arguments),
                        result=outcome.value,
                        is_error=outcome.is_error,
                    )
                )
                reply.evidence.extend(outcome.evidence)
                if outcome.pending_action_id is not None:
                    reply.pending_action_ids.append(outcome.pending_action_id)
        else:
            messages.append({"role": "system", "content": _OUT_OF_ROUNDS_NOTE})
            completion = create_chat_completion(self._client, self._model, messages)
            content = completion.choices[0].message.content or ""
            reply.content = content.strip() or _EMPTY_REPLY

        reply.mode = derive_mode(call.name for call in reply.tool_calls)
        return reply


def derive_mode(tool_names: Any) -> ChatMode:
    names = set(tool_names)
    used_policy = bool(names & POLICY_TOOLS)
    used_business = bool(names & BUSINESS_TOOLS)
    if used_policy and used_business:
        return ChatMode.MIXED
    if used_policy:
        return ChatMode.RAG
    if used_business:
        return ChatMode.AGENT
    return ChatMode.GENERAL


def _parsed(raw_arguments: str) -> Any:
    try:
        return json.loads(raw_arguments or "{}")
    except json.JSONDecodeError:
        return {"raw": raw_arguments}
