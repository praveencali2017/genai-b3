"""Chat request and response bodies."""

from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Self

from pydantic import BaseModel, ConfigDict, StringConstraints

from ai_travel_agent.models import ChatMessage, PendingAction
from ai_travel_agent.models.base import utc_now
from ai_travel_agent.models.enums import (
    ChatMode,
    MessageRole,
    PendingActionStatus,
    PendingActionType,
)

MAX_MESSAGE_LENGTH = 4000


class MessageCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True, min_length=1, max_length=MAX_MESSAGE_LENGTH
        ),
    ]


class Decision(StrEnum):
    CONFIRM = "CONFIRM"
    REJECT = "REJECT"


class ActionDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Decision


class ChatSessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    created_at: datetime
    updated_at: datetime


class Citation(BaseModel):
    document_id: int
    filename: str
    version: int
    page: int | None
    chunk_id: str


class MessageError(BaseModel):
    code: str
    message: str


class PendingActionResponse(BaseModel):
    id: int
    action: PendingActionType
    status: PendingActionStatus
    terms: dict[str, Any]
    result: dict[str, Any] | None
    expires_at: datetime

    @classmethod
    def from_action(cls, action: PendingAction) -> Self:
        # Expiry is persisted when someone decides; until then report it.
        status = action.status
        if status is PendingActionStatus.PENDING and action.expires_at <= utc_now():
            status = PendingActionStatus.EXPIRED
        return cls(
            id=action.id,
            action=action.action,
            status=status,
            terms=action.payload,
            result=action.result,
            expires_at=action.expires_at,
        )


class ChatMessageResponse(BaseModel):
    id: int
    seq_no: int
    role: MessageRole
    content: str
    mode: ChatMode | None
    citations: list[Citation]
    pending_actions: list[PendingActionResponse]
    error: MessageError | None
    created_at: datetime

    @classmethod
    def from_message(
        cls, message: ChatMessage, actions: Mapping[int, PendingAction]
    ) -> Self:
        metadata = message.message_metadata or {}
        action_ids = metadata.get("pending_action_ids", [])
        return cls(
            id=message.id,
            seq_no=message.seq_no,
            role=message.role,
            content=message.content,
            mode=metadata.get("mode"),
            citations=metadata.get("citations", []),
            pending_actions=[
                PendingActionResponse.from_action(actions[action_id])
                for action_id in action_ids
                if action_id in actions
            ],
            error=metadata.get("error"),
            created_at=message.created_at,
        )


class ChatTurnResponse(BaseModel):
    user_message: ChatMessageResponse
    assistant_message: ChatMessageResponse


class ActionDecisionResponse(BaseModel):
    action: PendingActionResponse
    message: ChatMessageResponse
