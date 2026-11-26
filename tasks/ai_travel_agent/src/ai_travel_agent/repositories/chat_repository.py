from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ai_travel_agent.models import ChatMessage, ChatSession, ToolRun
from ai_travel_agent.models.base import utc_now
from ai_travel_agent.models.enums import MessageRole


class ChatRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    # ------------------------------------------------------------ sessions

    def add_session(self, chat_session: ChatSession) -> ChatSession:
        self._session.add(chat_session)
        self._session.flush()
        return chat_session

    def get_session(self, session_id: int) -> ChatSession | None:
        return self._session.get(ChatSession, session_id)

    def lock_session(self, session_id: int) -> ChatSession | None:
        """`SELECT ... FOR UPDATE` the session row. Taken before numbering a new
        message, so two concurrent messages can't get the same `seq_no`."""
        return self._session.scalars(
            select(ChatSession)
            .where(ChatSession.id == session_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).one_or_none()

    def list_sessions(self, user_id: int) -> Sequence[ChatSession]:
        return self._session.scalars(
            select(ChatSession)
            .where(ChatSession.user_id == user_id)
            .order_by(ChatSession.updated_at.desc(), ChatSession.id.desc())
        ).all()

    # ------------------------------------------------------------ messages

    def append_message(
        self,
        chat_session: ChatSession,
        role: MessageRole,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> ChatMessage:
        """Add the next message of a session whose row the caller has locked."""
        next_seq = (
            self._session.scalar(
                select(func.coalesce(func.max(ChatMessage.seq_no), 0)).where(
                    ChatMessage.session_id == chat_session.id
                )
            )
            or 0
        ) + 1
        message = ChatMessage(
            session_id=chat_session.id,
            seq_no=next_seq,
            role=role,
            content=content,
            message_metadata=metadata or {},
        )
        self._session.add(message)
        chat_session.updated_at = utc_now()
        self._session.flush()
        return message

    def add_tool_runs(self, message: ChatMessage, runs: Sequence[ToolRun]) -> None:
        for run in runs:
            run.message_id = message.id
            self._session.add(run)
        self._session.flush()

    def list_messages(
        self, session_id: int, after_seq: int = 0, limit: int = 100
    ) -> Sequence[ChatMessage]:
        """Messages after `after_seq`, oldest first."""
        return self._session.scalars(
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id, ChatMessage.seq_no > after_seq)
            .order_by(ChatMessage.seq_no)
            .limit(limit)
        ).all()

    def recent_messages(self, session_id: int, limit: int) -> list[ChatMessage]:
        """The newest `limit` messages, returned oldest first."""
        newest_first = self._session.scalars(
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.seq_no.desc())
            .limit(limit)
        ).all()
        return list(reversed(newest_first))
