"""Who am I: lets the web UI check a login and show admin tools."""

from fastapi import APIRouter

from ai_travel_agent.controllers.responses import AUTH_ERRORS
from ai_travel_agent.schemas.auth import MeResponse
from ai_travel_agent.security.basic_auth import CurrentUser

router = APIRouter(prefix="/api/auth", tags=["auth"], responses=AUTH_ERRORS)


@router.get("/me")
def who_am_i(user: CurrentUser) -> MeResponse:
    return MeResponse.from_user(user)
