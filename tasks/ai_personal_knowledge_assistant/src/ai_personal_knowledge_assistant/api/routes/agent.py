"""Agent endpoint: chat with an agent that picks document tools on its own."""

from dataclasses import asdict
from typing import Annotated, Any

from fastapi import APIRouter, Depends, status

from ai_personal_knowledge_assistant.agent.runner import SingleAgent
from ai_personal_knowledge_assistant.api.conversation import (
    load_history,
    save_exchange,
)
from ai_personal_knowledge_assistant.api.dependencies import (
    Container,
    SessionFactory,
    get_agent,
)
from ai_personal_knowledge_assistant.api.schemas import (
    AgentChatRequest,
    AgentChatResponse,
    ErrorResponse,
    ToolCallOut,
)

router = APIRouter(prefix="/agent", tags=["agent"])


def _error(description: str) -> dict[str, Any]:
    return {"model": ErrorResponse, "description": description}


@router.post(
    "/chat",
    summary="Chat with the document agent, which chooses its own tools",
    responses={
        status.HTTP_502_BAD_GATEWAY: _error(
            "The OpenAI call failed, or the agent didn't finish within "
            "AGENT_MAX_ROUNDS steps."
        ),
        status.HTTP_503_SERVICE_UNAVAILABLE: _error(
            "OPENAI_API_KEY is not set, or the vector store failed."
        ),
    },
)
def agent_chat(
    request: AgentChatRequest,
    agent: Annotated[SingleAgent, Depends(get_agent)],
    session_factory: SessionFactory,
    container: Container,
) -> AgentChatResponse:
    """The agent decides which tools to call (`list_documents`,
    `search_documents`, `answer_from_documents`) and replies from their
    results only. The response lists the tools it used and the sources of its
    document answers. The exchange, with both, is added to the chat session,
    which is created if `session_id` is omitted or unknown; nothing is stored
    if the agent fails."""
    history = load_history(
        session_factory, request.session_id, container.settings.chat_history_limit
    )
    run = agent.run(request.message, history)
    session_id = save_exchange(
        session_factory,
        request.session_id,
        request.message,
        run.output,
        sources=[asdict(source) for source in run.sources],
        # The trace as the client sees it; full tool results aren't kept.
        tool_calls=[
            ToolCallOut.from_tool_call(call).model_dump(mode="json")
            for call in run.tool_calls
        ],
    )
    return AgentChatResponse.from_run(run, session_id)
