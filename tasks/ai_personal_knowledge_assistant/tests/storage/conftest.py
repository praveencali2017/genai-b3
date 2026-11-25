"""Fixtures for the storage tests: a fresh in-memory SQLite database per test."""

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from ai_personal_knowledge_assistant.storage.database import (
    create_db_engine,
    create_session_factory,
    init_db,
)


@pytest.fixture
def engine() -> Iterator[Engine]:
    engine = create_db_engine("sqlite://")
    init_db(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def session_factory(engine: Engine) -> sessionmaker[Session]:
    return create_session_factory(engine)


@pytest.fixture
def session(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    with session_factory() as session:
        yield session
