"""Composition root: builds the long-lived services the API shares.

The app's lifespan opens one `AppContainer` and keeps it on `app.state` for the
life of the process; route dependencies read from it (see
`controllers/dependencies.py`).
"""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from ai_restaurant_ordering_assistant.config.database import (
    create_db_engine,
    create_session_factory,
)
from ai_restaurant_ordering_assistant.config.settings import Settings


@dataclass(frozen=True, slots=True)
class AppContainer:
    settings: Settings
    engine: Engine
    session_factory: sessionmaker[Session]


@contextmanager
def open_container(settings: Settings) -> Iterator[AppContainer]:
    """Wire the services; the database engine is disposed when the block exits."""
    engine = create_db_engine(settings.database_url.get_secret_value())
    try:
        yield AppContainer(
            settings=settings,
            engine=engine,
            session_factory=create_session_factory(engine),
        )
    finally:
        engine.dispose()
