"""Error responses documented in OpenAPI, so Swagger shows the error body."""

from typing import Any

from ai_travel_agent.schemas.error import ErrorResponse

ResponsesDoc = dict[int | str, dict[str, Any]]


def _error(description: str) -> dict[str, Any]:
    return {"model": ErrorResponse, "description": description}


AUTH_ERRORS: ResponsesDoc = {401: _error("Missing or invalid credentials")}
ADMIN_ERRORS: ResponsesDoc = {**AUTH_ERRORS, 403: _error("Administrators only")}
NOT_FOUND: ResponsesDoc = {404: _error("Not found, or not yours")}
CONFLICT: ResponsesDoc = {
    409: _error("Not enough seats, departed trip, bad status change or key reuse")
}
