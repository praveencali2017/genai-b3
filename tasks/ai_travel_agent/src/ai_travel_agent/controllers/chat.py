"""Chat endpoints: sessions, history, messages and action confirmation.

These handlers manage their own transactions inside the services (a turn must
commit the user's message before calling the model), so they take no DbSession.
They are plain `def` so FastAPI runs them in its thread pool: the agent calls
this same API over HTTP while a turn is in progress.
"""

from typing import Annotated

from fastapi import APIRouter, Path, status

from ai_travel_agent.container import AppContainer
from ai_travel_agent.controllers.dependencies import Container
from ai_travel_agent.controllers.responses import (
    AUTH_ERRORS,
    CONFLICT,
    NOT_FOUND,
)
from ai_travel_agent.schemas.chat import (
    ActionDecision,
    ActionDecisionResponse,
    ChatMessageResponse,
    ChatSessionResponse,
    ChatTurnResponse,
    MessageCreate,
)
from ai_travel_agent.schemas.error import ErrorResponse
from ai_travel_agent.security.basic_auth import CurrentUser
from ai_travel_agent.services.chat_service import ChatService
from ai_travel_agent.services.pending_action_service import (
    PendingActionService,
)

router = APIRouter(prefix="/api/chat", tags=["chat"], responses=AUTH_ERRORS)

SessionId = Annotated[int, Path(gt=0)]
ActionId = Annotated[int, Path(gt=0)]
AI_ERRORS = {
    429: {"model": ErrorResponse, "description": "Too many messages"},
    503: {"model": ErrorResponse, "description": "AI service unavailable"},
}


def _chat(container: AppContainer) -> ChatService:
    return container.chat_service


@router.post("/sessions", status_code=status.HTTP_201_CREATED)
def create_session(container: Container, user: CurrentUser) -> ChatSessionResponse:
    chat = _chat(container).create_session(user)
    return ChatSessionResponse.model_validate(chat)


@router.get("/sessions")
def list_sessions(container: Container, user: CurrentUser) -> list[ChatSessionResponse]:
    """The user's chat sessions, most recently active first."""
    sessions = _chat(container).list_sessions(user)
    return [ChatSessionResponse.model_validate(s) for s in sessions]


@router.get("/sessions/{session_id}/messages", responses=NOT_FOUND)
def list_messages(
    session_id: SessionId, container: Container, user: CurrentUser
) -> list[ChatMessageResponse]:
    """The full history, oldest first, with each reply's proposals."""
    return _chat(container).list_messages(user, session_id)


@router.post("/sessions/{session_id}/messages", responses={**NOT_FOUND, **AI_ERRORS})
def send_message(
    session_id: SessionId,
    body: MessageCreate,
    container: Container,
    user: CurrentUser,
) -> ChatTurnResponse:
    """Ask the assistant. Bookings and cancellations come back as pending
    actions that must be confirmed with the endpoint below."""
    return _chat(container).send_message(user, session_id, body.content)


@router.post(
    "/actions/{action_id}/confirm",
    responses={**NOT_FOUND, **CONFLICT, **AI_ERRORS},
)
def decide_action(
    action_id: ActionId,
    body: ActionDecision,
    container: Container,
    user: CurrentUser,
) -> ActionDecisionResponse:
    """Confirm or reject a proposal. Repeating a decision returns the same
    outcome; a booking is never made twice."""
    service = PendingActionService(
        container.session_factory, container.api_client_factory
    )
    return service.decide(user, action_id, body.decision)
