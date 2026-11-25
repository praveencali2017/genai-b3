"""FastAPI dependencies shared by the routers."""

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from ai_restaurant_ordering_assistant.config.database import session_scope
from ai_restaurant_ordering_assistant.container import AppContainer


def get_container(request: Request) -> AppContainer:
    return request.app.state.container


Container = Annotated[AppContainer, Depends(get_container)]


def get_db_session(container: Container) -> Iterator[Session]:
    """One transaction per request: committed if the route returns, rolled
    back if it raises."""
    with session_scope(container.session_factory) as session:
        yield session


DbSession = Annotated[Session, Depends(get_db_session)]
