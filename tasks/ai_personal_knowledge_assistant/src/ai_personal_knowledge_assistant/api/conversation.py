"""Chat history around a model call, shared by the chat and agent routes.

A route loads the history in one short transaction, calls the model with no
transaction open, then saves the exchange in a second transaction (see
`dependencies.get_session_factory` for why). If the model call fails, the
second transaction never runs, so a failed request stores nothing.
"""

from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from ai_personal_knowledge_assistant.rag.answerer import ChatTurn
from ai_personal_knowledge_assistant.storage.chat_history_repository import (
    ChatHistoryRepository,
)
from ai_personal_knowledge_assistant.storage.database import session_scope
from ai_personal_knowledge_assistant.storage.records import JsonObject, MessageRole


def load_history(
    session_factory: sessionmaker[Session], session_id: UUID | None, limit: int
) -> list[ChatTurn]:
    """The last `limit` messages of the session, oldest first; none for a new one."""
    if session_id is None:
        return []
    with session_scope(session_factory) as session:
        messages = ChatHistoryRepository(session).recent_messages(session_id, limit)
    return [
        ChatTurn(role=message.role, content=message.content) for message in messages
    ]


def save_exchange(
    session_factory: sessionmaker[Session],
    session_id: UUID | None,
    user_message: str,
    reply: str,
    *,
    sources: list[JsonObject],
    tool_calls: list[JsonObject] | None = None,
) -> UUID:
    """Store the user message and the reply in one transaction.

    The session is created if `session_id` is None or unknown. Returns its id.
    """
    with session_scope(session_factory) as session:
        repository = ChatHistoryRepository(session)
        chat_session = repository.get_or_create_session(session_id)
        repository.append_message(chat_session.id, MessageRole.USER, user_message)
        repository.append_message(
            chat_session.id,
            MessageRole.ASSISTANT,
            reply,
            sources=sources,
            tool_calls=tool_calls,
        )
    return chat_session.id
