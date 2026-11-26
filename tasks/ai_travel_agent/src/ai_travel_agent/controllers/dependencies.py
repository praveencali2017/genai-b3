"""FastAPI dependencies shared by the routers."""

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from ai_travel_agent.config.database import session_scope
from ai_travel_agent.container import AppContainer


def get_container(request: Request) -> AppContainer:
    return request.app.state.container


Container = Annotated[AppContainer, Depends(get_container)]


def get_db_session(container: Container) -> Iterator[Session]:
    """One transaction per request: committed when the route returns, rolled
    back if it raises."""
    with session_scope(container.session_factory) as session:
        yield session


# scope="function": commit before the response is sent, so a failed commit
# becomes an error response instead of a success the client already received.
DbSession = Annotated[Session, Depends(get_db_session, scope="function")]
