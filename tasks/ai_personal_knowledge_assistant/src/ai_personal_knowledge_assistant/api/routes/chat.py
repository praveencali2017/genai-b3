"""Chat endpoint: ask a question, get an answer grounded in the documents."""

from dataclasses import asdict
from typing import Annotated, Any

from fastapi import APIRouter, Depends, status

from ai_personal_knowledge_assistant.api.conversation import (
    load_history,
    save_exchange,
)
from ai_personal_knowledge_assistant.api.dependencies import (
    Container,
    SessionFactory,
    get_answerer,
)
from ai_personal_knowledge_assistant.api.schemas import (
    AskRequest,
    AskResponse,
    ErrorResponse,
)
from ai_personal_knowledge_assistant.rag.answerer import GroundedAnswerer

router = APIRouter(prefix="/chat", tags=["chat"])


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
    history = load_history(
        session_factory, request.session_id, container.settings.chat_history_limit
    )
    answer = answerer.answer(
        request.question, document_ids=request.document_ids, history=history
    )
    session_id = save_exchange(
        session_factory,
        request.session_id,
        request.question,
        answer.text,
        sources=[asdict(source) for source in answer.sources],
    )
    return AskResponse.from_answer(answer, session_id)
