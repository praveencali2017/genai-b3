"""An app wired like production, but on temporary folders and a fake embedder.

The real lifespan runs (`with TestClient(app)`), so the tests also cover the
container wiring; only the storage locations and the embedding model differ.
"""

from collections.abc import Iterator
from contextlib import AbstractContextManager
from pathlib import Path
from typing import cast
from uuid import UUID

import openai
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from ai_personal_knowledge_assistant.api.container import AppContainer, open_container
from ai_personal_knowledge_assistant.api.main import create_app
from ai_personal_knowledge_assistant.core.settings import Settings
from ai_personal_knowledge_assistant.storage.chat_history_repository import (
    ChatHistoryRepository,
)
from ai_personal_knowledge_assistant.storage.models import ChatMessage, ChatSession
from ai_personal_knowledge_assistant.storage.records import ChatMessageRecord
from tests.conftest import TEST_COLLECTION
from tests.fakes import FakeEmbedder, FakeOpenAI

SAMPLE_DOCS_DIR = Path(__file__).resolve().parents[2] / "sample_docs"


@pytest.fixture
def sample_docs_dir() -> Path:
    return SAMPLE_DOCS_DIR


@pytest.fixture
def settings(tmp_path: Path, release_chroma_clients: None) -> Settings:
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'app.db'}",
        chroma_dir=tmp_path / "chroma",
        chroma_collection=TEST_COLLECTION,
        upload_dir=tmp_path / "uploads",
        max_upload_mb=1,
    )


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    def open_test_container() -> AbstractContextManager[AppContainer]:
        return open_container(settings, embedder=FakeEmbedder())

    return create_app(open_test_container)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as client:
        yield client


@pytest.fixture
def container(client: TestClient) -> AppContainer:
    """The services the running app uses, for inspecting what it stored."""
    return client.app.state.container  # type: ignore[attr-defined]


def app_with_client(settings: Settings, client: FakeOpenAI | None) -> FastAPI:
    """The app on `settings`, with `client` (None: no API key) as its OpenAI."""

    def open_test_container() -> AbstractContextManager[AppContainer]:
        return open_container(
            settings,
            embedder=FakeEmbedder(),
            openai_client_factory=lambda _settings: cast(openai.OpenAI | None, client),
        )

    return create_app(open_test_container)


def stored_messages(
    container: AppContainer, session_id: str
) -> list[ChatMessageRecord]:
    with container.session_factory() as session:
        return ChatHistoryRepository(session).recent_messages(UUID(session_id), 100)


def stored_counts(container: AppContainer) -> tuple[int, int]:
    """(chat sessions, chat messages) in the database."""
    with container.session_factory() as session:
        sessions = session.scalar(select(func.count()).select_from(ChatSession))
        messages = session.scalar(select(func.count()).select_from(ChatMessage))
    return sessions or 0, messages or 0
