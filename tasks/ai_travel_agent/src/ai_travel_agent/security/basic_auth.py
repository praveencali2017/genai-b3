"""Who is calling: HTTP Basic for people, an internal identity for the agent.

People (browser, Swagger, curl) send HTTP Basic credentials, checked against the
Argon2 hash on every request.

The chatbot's agent calls this same API over HTTP so that every rule, role and
ownership check applies to it unchanged. It never holds the user's password;
instead it sends `X-Internal-Token` (a random secret generated when the process
starts, never logged or sent to the browser or the model) together with
`X-Acting-User-Id`, the id of the user whose chat message is being handled. The
model can't choose either header: the agent's HTTP client sets them from the
server-side session.
"""

import secrets
from typing import Annotated

from fastapi import Depends, Header
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from ai_travel_agent.controllers.dependencies import (
    Container,
    DbSession,
)
from ai_travel_agent.exceptions.errors import (
    AuthenticationError,
    PermissionDeniedError,
)
from ai_travel_agent.models import User
from ai_travel_agent.repositories.user_repository import (
    UserRepository,
)
from ai_travel_agent.security.internal_identity import (
    ACTING_USER_HEADER,
    INTERNAL_TOKEN_HEADER,
)
from ai_travel_agent.security.passwords import verify_password

_basic = HTTPBasic(
    auto_error=False,
    description="Demo accounts are listed in the README.",
)


def get_optional_user(
    container: Container,
    session: DbSession,
    credentials: Annotated[HTTPBasicCredentials | None, Depends(_basic)],
    internal_token: Annotated[
        str | None, Header(alias=INTERNAL_TOKEN_HEADER, include_in_schema=False)
    ] = None,
    acting_user_id: Annotated[
        int | None, Header(alias=ACTING_USER_HEADER, include_in_schema=False)
    ] = None,
) -> User | None:
    """The caller, None if they sent no credentials at all; wrong credentials
    raise `AuthenticationError` rather than silently making them anonymous."""
    users = UserRepository(session)
    if internal_token is not None:
        return _internal_caller(
            users, internal_token, acting_user_id, container.internal_token
        )
    if credentials is None:
        return None
    user = users.get_by_username(credentials.username)
    password_hash = user.password_hash if user is not None else None
    if not verify_password(credentials.password, password_hash) or user is None:
        raise AuthenticationError("Incorrect username or password.")
    return user


def get_current_user(user: Annotated[User | None, Depends(get_optional_user)]) -> User:
    if user is None:
        raise AuthenticationError()
    return user


def require_admin(user: Annotated[User, Depends(get_current_user)]) -> User:
    if not user.is_admin:
        raise PermissionDeniedError()
    return user


OptionalUser = Annotated[User | None, Depends(get_optional_user)]
CurrentUser = Annotated[User, Depends(get_current_user)]
AdminUser = Annotated[User, Depends(require_admin)]


def _internal_caller(
    users: UserRepository,
    presented_token: str,
    acting_user_id: int | None,
    expected_token: str,
) -> User:
    if not secrets.compare_digest(presented_token, expected_token):
        raise AuthenticationError("Invalid internal credentials.")
    user = users.get(acting_user_id) if acting_user_id is not None else None
    if user is None:
        raise AuthenticationError("Invalid internal credentials.")
    return user
