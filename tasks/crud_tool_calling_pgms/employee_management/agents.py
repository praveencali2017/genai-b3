"""Chat assistants over the employee tools, in three architectures:

- tool calling: one model loop with all five tools (the original chatbot);
- single agent: one agent with all five tools that supervises its own work
  (plan -> read -> write -> verify), one tool call at a time;
- multi-agent: a supervisor that delegates to a read agent (read tools only) and
  a write agent (write tools only), calling them as tools.

Every architecture runs on the same loop, `run_agent`, and returns an
`AgentRun` trace of what each agent did, so the UI can show it.
"""

import json
import logging
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from functools import cache, partial
from typing import Annotated, Any, Literal

import openai
from pydantic import BaseModel, Field

from prompts import (
    read_agent_instructions,
    single_agent_instructions,
    supervisor_instructions,
    tool_calling_instructions,
    write_agent_instructions,
)
from tools import READ_TOOLS, TOOLS, WRITE_TOOLS, function_tool, object_schema, run_tool

DEFAULT_OPENAI_MODEL = "gpt-5.4-mini"
OPENAI_TIMEOUT_SECONDS = 30.0
MAX_HISTORY_MESSAGES = 20
# Round limits guard against a model that keeps calling tools without answering.
# The self-supervising agent calls one tool per round, so it needs a few more.
TOOL_CALLING_MAX_ROUNDS = 6
SINGLE_AGENT_MAX_ROUNDS = 8
SUPERVISOR_MAX_ROUNDS = 6
SUB_AGENT_MAX_ROUNDS = 4

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------- public models


class ChatMode(StrEnum):
    TOOL_CALLING = "tool_calling"
    SINGLE_AGENT = "single_agent"
    MULTI_AGENT = "multi_agent"


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class ToolStep(BaseModel):
    """One call to a data tool."""

    kind: Literal["tool"] = "tool"
    tool: str
    # Parsed JSON arguments, or the raw text when the model sent invalid JSON.
    arguments: Any
    result: Any
    is_error: bool


class DelegationStep(BaseModel):
    """A task handed to another agent; `run` is that agent's own trace."""

    kind: Literal["delegate"] = "delegate"
    run: "AgentRun"


Step = Annotated[ToolStep | DelegationStep, Field(discriminator="kind")]


class AgentRun(BaseModel):
    agent: str
    input: str
    output: str
    steps: list[Step] = Field(default_factory=list)
    # True when the agent stopped without answering (it ran out of rounds).
    is_error: bool = False


DelegationStep.model_rebuild()  # resolve the forward reference to AgentRun


# ---------------------------------------------------------------- errors


class AssistantNotConfiguredError(RuntimeError):
    pass


class AssistantUnavailableError(RuntimeError):
    pass


class AgentStepLimitError(RuntimeError):
    """The agent used all its rounds without answering; `run` holds its partial trace."""

    def __init__(self, run: AgentRun) -> None:
        super().__init__(run.output)
        self.run = run


# ---------------------------------------------------------------- agent loop

# Runs one tool call from its raw JSON arguments. Returns the step for the trace
# and the text that goes back to the model.
StepHandler = Callable[[str], tuple[ToolStep | DelegationStep, str]]


@dataclass(frozen=True)
class AgentSpec:
    name: str
    build_instructions: Callable[[date], str]
    tools: list[dict[str, Any]]
    handlers: Mapping[str, StepHandler]
    max_rounds: int
    # False forces one tool call per round, so a step can depend on the last result.
    parallel_tool_calls: bool = True


def openai_model() -> str:
    """Read per request: .env is loaded by main.py after this module is imported."""
    return os.getenv("OPENAI_MODEL", "").strip() or DEFAULT_OPENAI_MODEL


@cache
def openai_client(api_key: str) -> openai.OpenAI:
    return openai.OpenAI(api_key=api_key, timeout=OPENAI_TIMEOUT_SECONDS, max_retries=1)


def require_client() -> openai.OpenAI:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise AssistantNotConfiguredError(
            "Chat isn't configured: set OPENAI_API_KEY in .env."
        )
    return openai_client(api_key)


def _final_message_text(response: Any) -> str:
    """Text of the last assistant message. `response.output_text` joins every
    message item, and the model sometimes emits a draft and a final version,
    which would show the user the same answer twice."""
    messages = [item for item in response.output if item.type == "message"]
    if not messages:
        return ""
    parts = [part.text for part in messages[-1].content if part.type == "output_text"]
    return "".join(parts).strip()


def _latest_user_text(conversation: list[dict[str, Any]]) -> str:
    return next(
        (
            item["content"]
            for item in reversed(conversation)
            if item.get("role") == "user"
        ),
        "",
    )


def _parse_arguments(raw_arguments: str) -> Any:
    try:
        return json.loads(raw_arguments or "{}")
    except json.JSONDecodeError:
        return raw_arguments


def _unknown_tool(name: str, raw_arguments: str) -> tuple[ToolStep, str]:
    # The model only sees its own tools, but a name outside them must never run,
    # e.g. the read agent must not reach a write tool.
    error = {"error": f"Unknown tool {name!r}."}
    step = ToolStep(
        tool=name,
        arguments=_parse_arguments(raw_arguments),
        result=error,
        is_error=True,
    )
    return step, json.dumps(error)


def run_agent(
    spec: AgentSpec, conversation: list[dict[str, Any]], client: openai.OpenAI
) -> AgentRun:
    """Run the tool-calling loop until the agent answers in text."""
    items: list[Any] = list(conversation)
    steps: list[ToolStep | DelegationStep] = []
    task = _latest_user_text(conversation)

    for _ in range(spec.max_rounds):
        try:
            response = client.responses.create(
                model=openai_model(),
                instructions=spec.build_instructions(date.today()),
                input=items,
                tools=spec.tools,
                parallel_tool_calls=spec.parallel_tool_calls,
            )
        except openai.OpenAIError as error:
            logger.exception(
                "OpenAI request failed (agent=%s, model=%s)", spec.name, openai_model()
            )
            raise AssistantUnavailableError(
                f"The assistant is unavailable: {error}"
            ) from error

        tool_calls = [item for item in response.output if item.type == "function_call"]
        if not tool_calls:
            output = _final_message_text(response) or "(no answer)"
            return AgentRun(agent=spec.name, input=task, output=output, steps=steps)

        items.extend(response.output)
        for call in tool_calls:
            handler = spec.handlers.get(call.name)
            step, output = (
                handler(call.arguments)
                if handler
                else _unknown_tool(call.name, call.arguments)
            )
            steps.append(step)
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": call.call_id,
                    "output": output,
                }
            )

    raise AgentStepLimitError(
        AgentRun(
            agent=spec.name,
            input=task,
            output=f"{spec.name} took too many steps without finishing.",
            steps=steps,
            is_error=True,
        )
    )


# ---------------------------------------------------------------- tool handlers


def _run_data_tool(name: str, raw_arguments: str) -> tuple[ToolStep, str]:
    result = run_tool(name, raw_arguments)
    step = ToolStep(
        tool=name,
        arguments=_parse_arguments(raw_arguments),
        result=result.value,
        is_error=result.is_error,
    )
    return step, result.to_json()


def _data_tool_handlers(tools: list[dict[str, Any]]) -> dict[str, StepHandler]:
    return {tool["name"]: partial(_run_data_tool, tool["name"]) for tool in tools}


def _delegate(
    tool_name: str, agent: AgentSpec, client: openai.OpenAI, raw_arguments: str
) -> tuple[ToolStep | DelegationStep, str]:
    arguments = _parse_arguments(raw_arguments)
    task = arguments.get("task", "").strip() if isinstance(arguments, dict) else ""
    if not task:
        error = {"error": "Give the agent a non-empty task."}
        return ToolStep(
            tool=tool_name, arguments=arguments, result=error, is_error=True
        ), json.dumps(error)

    try:
        run = run_agent(agent, [{"role": "user", "content": task}], client)
    except AgentStepLimitError as error:
        # Report the failure to the supervisor so it can explain it, instead of
        # failing the whole request.
        run = error.run
    result = {"error": run.output} if run.is_error else {"answer": run.output}
    return DelegationStep(run=run), json.dumps(result)


def _delegation_tool(name: str, description: str) -> dict[str, Any]:
    return function_tool(
        name,
        description,
        object_schema(
            {
                "task": {
                    "type": "string",
                    "description": "One self-contained task in plain words, with "
                    "every id, name and value the agent needs. The agent can't "
                    "see the conversation.",
                }
            },
            ["task"],
        ),
    )


ASK_READ_AGENT = "ask_read_agent"
ASK_WRITE_AGENT = "ask_write_agent"
SUPERVISOR_TOOLS: list[dict[str, Any]] = [
    _delegation_tool(
        ASK_READ_AGENT,
        "Ask the read agent to look up employees or compute facts from them (search, "
        "list, one employee by id, counts, totals, averages). It can't change data.",
    ),
    _delegation_tool(
        ASK_WRITE_AGENT,
        "Ask the write agent to create, update or delete exactly ONE employee. "
        "Include the numeric id for updates and deletes, the exact new values, and "
        "for a delete, that the user confirmed deleting that specific employee.",
    ),
]


# ---------------------------------------------------------------- architectures

TOOL_CALLING_AGENT = AgentSpec(
    name="assistant",
    build_instructions=tool_calling_instructions,
    tools=TOOLS,
    handlers=_data_tool_handlers(TOOLS),
    max_rounds=TOOL_CALLING_MAX_ROUNDS,
)

SINGLE_AGENT = AgentSpec(
    name="single_agent",
    build_instructions=single_agent_instructions,
    tools=TOOLS,
    handlers=_data_tool_handlers(TOOLS),
    max_rounds=SINGLE_AGENT_MAX_ROUNDS,
    parallel_tool_calls=False,
)

READ_AGENT = AgentSpec(
    name="read_agent",
    build_instructions=read_agent_instructions,
    tools=READ_TOOLS,
    handlers=_data_tool_handlers(READ_TOOLS),
    max_rounds=SUB_AGENT_MAX_ROUNDS,
)

WRITE_AGENT = AgentSpec(
    name="write_agent",
    build_instructions=write_agent_instructions,
    tools=WRITE_TOOLS,
    handlers=_data_tool_handlers(WRITE_TOOLS),
    max_rounds=SUB_AGENT_MAX_ROUNDS,
    parallel_tool_calls=False,
)


def supervisor_agent(client: openai.OpenAI) -> AgentSpec:
    """Built per request because the delegation handlers need the client."""
    return AgentSpec(
        name="supervisor",
        build_instructions=supervisor_instructions,
        tools=SUPERVISOR_TOOLS,
        handlers={
            ASK_READ_AGENT: partial(_delegate, ASK_READ_AGENT, READ_AGENT, client),
            ASK_WRITE_AGENT: partial(_delegate, ASK_WRITE_AGENT, WRITE_AGENT, client),
        },
        max_rounds=SUPERVISOR_MAX_ROUNDS,
        # A write must never run in the same round as the lookup that finds its id.
        parallel_tool_calls=False,
    )


def _entry_agent(mode: ChatMode, client: openai.OpenAI) -> AgentSpec:
    match mode:
        case ChatMode.TOOL_CALLING:
            return TOOL_CALLING_AGENT
        case ChatMode.SINGLE_AGENT:
            return SINGLE_AGENT
        case ChatMode.MULTI_AGENT:
            return supervisor_agent(client)


def answer_chat(messages: list[ChatMessage], mode: ChatMode) -> AgentRun:
    """Answer the latest message with the chosen architecture. The reply is the
    returned run's `output`."""
    client = require_client()
    conversation: list[dict[str, Any]] = [
        {"role": m.role, "content": m.content} for m in messages[-MAX_HISTORY_MESSAGES:]
    ]
    return run_agent(_entry_agent(mode, client), conversation, client)
