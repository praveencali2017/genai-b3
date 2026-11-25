"""Chat endpoint: ask a question, get an answer grounded in the documents."""

from dataclasses import asdict
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session, sessionmaker

from ai_personal_knowledge_assistant.api.dependencies import (
    Container,
    get_answerer,
    get_session_factory,
)
from ai_personal_knowledge_assistant.api.schemas import (
    AskRequest,
    AskResponse,
    ErrorResponse,
)
from ai_personal_knowledge_assistant.rag.answerer import (
    Answer,
    ChatTurn,
    GroundedAnswerer,
)
from ai_personal_knowledge_assistant.storage.chat_history_repository import (
    ChatHistoryRepository,
)
from ai_personal_knowledge_assistant.storage.database import session_scope
from ai_personal_knowledge_assistant.storage.records import MessageRole

router = APIRouter(prefix="/chat", tags=["chat"])

SessionFactory = Annotated[sessionmaker[Session], Depends(get_session_factory)]


def _error(description: str) -> dict[str, Any]:
    return {"model": ErrorResponse, "description": description}


@router.post(
    "/ask",
    summary="Answer a question from the uploaded documents, with sources",
    responses={
        status.HTTP_502_BAD_GATEWAY: _error("The OpenAI call failed."),
        status.HTTP_503_SERVICE_UNAVAILABLE: _error(
            "OPENAI_API_KEY is not set, or the vector store failed."
        ),
    },
)
def ask(
    request: AskRequest,
    answerer: Annotated[GroundedAnswerer, Depends(get_answerer)],
    session_factory: SessionFactory,
    container: Container,
) -> AskResponse:
    """Retrieve the relevant excerpts and answer only from them, citing [n].
    When the documents don't cover the question the answer says so
    (`is_grounded: false`). The question and answer are added to the chat
    session, which is created if `session_id` is omitted or unknown; nothing is
    stored if answering fails."""
    history = _load_history(
        session_factory, request.session_id, container.settings.chat_history_limit
    )
    answer = answerer.answer(
        request.question, document_ids=request.document_ids, history=history
    )
    session_id = _save_exchange(
        session_factory, request.session_id, request.question, answer
    )
    return AskResponse.from_answer(answer, session_id)


def _load_history(
    session_factory: sessionmaker[Session], session_id: UUID | None, limit: int
) -> list[ChatTurn]:
    if session_id is None:
        return []
    with session_scope(session_factory) as session:
        messages = ChatHistoryRepository(session).recent_messages(session_id, limit)
    return [
        ChatTurn(role=message.role, content=message.content) for message in messages
    ]


def _save_exchange(
    session_factory: sessionmaker[Session],
    session_id: UUID | None,
    question: str,
    answer: Answer,
) -> UUID:
    """Store the question and answer in one transaction; returns the session id."""
    with session_scope(session_factory) as session:
        repository = ChatHistoryRepository(session)
        chat_session = repository.get_or_create_session(session_id)
        repository.append_message(chat_session.id, MessageRole.USER, question)
        repository.append_message(
            chat_session.id,
            MessageRole.ASSISTANT,
            answer.text,
            sources=[asdict(source) for source in answer.sources],
        )
    return chat_session.id
