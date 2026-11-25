"""Tests for `ChatHistoryRepository`."""

import uuid
from datetime import UTC

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ai_personal_knowledge_assistant.core.errors import ChatSessionNotFoundError
from ai_personal_knowledge_assistant.storage.chat_history_repository import (
    ChatHistoryRepository,
)
from ai_personal_knowledge_assistant.storage.models import ChatMessage, ChatSession
from ai_personal_knowledge_assistant.storage.records import MessageRole


def test_create_session_generates_a_uuid(session: Session) -> None:
    repository = ChatHistoryRepository(session)

    first = repository.create_session()
    second = repository.create_session()

    assert isinstance(first.id, uuid.UUID)
    assert first.id != second.id
    assert first.created_at.tzinfo is UTC


def test_get_or_create_without_id_starts_a_new_session(session: Session) -> None:
    repository = ChatHistoryRepository(session)

    first = repository.get_or_create_session(None)
    second = repository.get_or_create_session(None)

    assert first.id != second.id


def test_get_or_create_with_known_id_returns_that_session(session: Session) -> None:
    repository = ChatHistoryRepository(session)
    created = repository.create_session()
    session.expire_all()

    fetched = repository.get_or_create_session(created.id)

    assert fetched == created


def test_get_or_create_with_unknown_id_adopts_it(session: Session) -> None:
    repository = ChatHistoryRepository(session)
    client_id = uuid.uuid4()

    adopted = repository.get_or_create_session(client_id)

    assert adopted.id == client_id
    assert repository.get_or_create_session(client_id) == adopted
    assert session.scalar(select(func.count()).select_from(ChatSession)) == 1


def test_append_message_to_unknown_session_raises(session: Session) -> None:
    missing = uuid.uuid4()

    with pytest.raises(ChatSessionNotFoundError) as caught:
        ChatHistoryRepository(session).append_message(
            missing, MessageRole.USER, "hello"
        )

    assert caught.value.session_id == missing


def test_recent_messages_are_chronological(session: Session) -> None:
    repository = ChatHistoryRepository(session)
    chat = repository.create_session()
    repository.append_message(chat.id, MessageRole.USER, "q1")
    repository.append_message(chat.id, MessageRole.ASSISTANT, "a1")
    repository.append_message(chat.id, MessageRole.USER, "q2")

    messages = repository.recent_messages(chat.id, limit=10)

    assert [(m.role, m.content) for m in messages] == [
        (MessageRole.USER, "q1"),
        (MessageRole.ASSISTANT, "a1"),
        (MessageRole.USER, "q2"),
    ]
    assert all(m.session_id == chat.id for m in messages)
    assert all(m.created_at.tzinfo is UTC for m in messages)


def test_recent_messages_limit_keeps_the_newest_in_order(session: Session) -> None:
    repository = ChatHistoryRepository(session)
    chat = repository.create_session()
    for index in range(5):
        repository.append_message(chat.id, MessageRole.USER, f"m{index}")

    messages = repository.recent_messages(chat.id, limit=2)

    assert [m.content for m in messages] == ["m3", "m4"]


def test_recent_messages_only_returns_the_given_session(session: Session) -> None:
    repository = ChatHistoryRepository(session)
    mine = repository.create_session()
    other = repository.create_session()
    repository.append_message(mine.id, MessageRole.USER, "mine")
    repository.append_message(other.id, MessageRole.USER, "other")

    assert [m.content for m in repository.recent_messages(mine.id, 10)] == ["mine"]


def test_recent_messages_of_unknown_session_is_empty(session: Session) -> None:
    assert ChatHistoryRepository(session).recent_messages(uuid.uuid4(), 5) == []


def test_recent_messages_rejects_non_positive_limit(session: Session) -> None:
    repository = ChatHistoryRepository(session)

    with pytest.raises(ValueError, match="limit"):
        repository.recent_messages(uuid.uuid4(), 0)


def test_sources_and_tool_calls_round_trip_as_json(session: Session) -> None:
    repository = ChatHistoryRepository(session)
    chat = repository.create_session()
    sources = [{"filename": "HR_Policy.pdf", "page": 2, "score": 0.81}]
    tool_calls = [
        {
            "tool": "search_documents",
            "arguments": {"query": "casual leave"},
            "is_error": False,
        }
    ]
    repository.append_message(
        chat.id,
        MessageRole.ASSISTANT,
        "12 days [1]",
        sources=sources,
        tool_calls=tool_calls,
    )
    session.expire_all()

    (stored,) = repository.recent_messages(chat.id, limit=1)

    assert stored.sources == sources
    assert stored.tool_calls == tool_calls


def test_missing_sources_are_stored_as_sql_null(session: Session) -> None:
    repository = ChatHistoryRepository(session)
    chat = repository.create_session()
    repository.append_message(chat.id, MessageRole.USER, "hi")
    session.expire_all()

    (stored,) = repository.recent_messages(chat.id, limit=1)
    null_rows = session.scalar(
        select(func.count())
        .select_from(ChatMessage)
        .where(ChatMessage.sources.is_(None), ChatMessage.tool_calls.is_(None))
    )

    assert (stored.sources, stored.tool_calls) == (None, None)
    assert null_rows == 1


def test_deleting_a_session_cascades_to_its_messages(session: Session) -> None:
    """Checks the database-level ON DELETE CASCADE (needs SQLite foreign keys
    on), by deleting with SQL rather than through the ORM relationship."""
    repository = ChatHistoryRepository(session)
    chat = repository.create_session()
    repository.append_message(chat.id, MessageRole.USER, "q")
    repository.append_message(chat.id, MessageRole.ASSISTANT, "a")

    session.execute(ChatSession.__table__.delete().where(ChatSession.id == chat.id))

    assert session.scalar(select(func.count()).select_from(ChatMessage)) == 0
