"""Chat history: sessions and the messages exchanged in them."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from ai_personal_knowledge_assistant.core.errors import ChatSessionNotFoundError
from ai_personal_knowledge_assistant.storage.models import ChatMessage, ChatSession
from ai_personal_knowledge_assistant.storage.records import (
    ChatMessageRecord,
    ChatSessionRecord,
    JsonObject,
    MessageRole,
)


class ChatHistoryRepository:
    """Reads and writes chat sessions and messages inside the caller's transaction.

    Like `DocumentRepository`, it flushes but never commits.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def create_session(self, session_id: UUID | None = None) -> ChatSessionRecord:
        """Start a session, with a fresh random id unless one is given."""
        chat_session = (
            ChatSession() if session_id is None else ChatSession(id=session_id)
        )
        self._session.add(chat_session)
        self._session.flush()
        return _session_to_record(chat_session)

    def get_or_create_session(self, session_id: UUID | None) -> ChatSessionRecord:
        """The session the client refers to, creating it if needed.

        An unknown id is adopted rather than rejected: the client may be holding
        an id from before the server's database was reset, and the useful
        behaviour is to carry on that conversation, not to fail it. Typing the
        id as `UUID` keeps arbitrary strings out.
        """
        if session_id is not None:
            existing = self._session.get(ChatSession, session_id)
            if existing is not None:
                return _session_to_record(existing)
        return self.create_session(session_id)

    def append_message(
        self,
        session_id: UUID,
        role: MessageRole,
        content: str,
        *,
        sources: list[JsonObject] | None = None,
        tool_calls: list[JsonObject] | None = None,
    ) -> ChatMessageRecord:
        """Add a message at the end of the session; the session must exist."""
        if self._session.get(ChatSession, session_id) is None:
            raise ChatSessionNotFoundError(session_id)
        message = ChatMessage(
            session_id=session_id,
            role=role,
            content=content,
            sources=sources,
            tool_calls=tool_calls,
        )
        self._session.add(message)
        self._session.flush()
        return _message_to_record(message)

    def recent_messages(self, session_id: UUID, limit: int) -> list[ChatMessageRecord]:
        """The last `limit` messages of the session, oldest first.

        That's the order the model needs them in as conversation history. An
        unknown session simply has no messages.
        """
        if limit < 1:
            raise ValueError(f"limit must be >= 1, got {limit}")
        newest_first = self._session.scalars(
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.id.desc())
            .limit(limit)
        ).all()
        return [_message_to_record(message) for message in reversed(newest_first)]


def _session_to_record(chat_session: ChatSession) -> ChatSessionRecord:
    return ChatSessionRecord(id=chat_session.id, created_at=chat_session.created_at)


def _message_to_record(message: ChatMessage) -> ChatMessageRecord:
    return ChatMessageRecord(
        id=message.id,
        session_id=message.session_id,
        role=message.role,
        content=message.content,
        sources=message.sources,
        tool_calls=message.tool_calls,
        created_at=message.created_at,
    )
