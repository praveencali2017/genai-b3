"""HTTP client for the knowledge assistant API, used by the Streamlit UI.

The UI talks to the backend only through this module. Every failure, whether
the server is unreachable or it answered with an error status, surfaces as one
`ApiError` whose message is fit to show the user as is.
"""

from typing import Any

import httpx

DEFAULT_TIMEOUT_SECONDS = 10.0
# Indexing a file and multi-step agent runs (several OpenAI calls) take a while.
SLOW_TIMEOUT_SECONDS = 120.0


class ApiError(Exception):
    """A request to the API failed; `message` is readable by the end user."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class KnowledgeApiClient:
    def __init__(
        self,
        base_url: str,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        """`transport` replaces httpx's network transport (tests pass a mock)."""
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._transport = transport

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/health")

    def list_documents(self) -> list[dict[str, Any]]:
        return self._request("GET", "/documents")

    def upload(self, filename: str, content: bytes) -> dict[str, Any]:
        return self._request(
            "POST",
            "/documents/upload",
            files={"file": (filename, content)},
            timeout=SLOW_TIMEOUT_SECONDS,
        )

    def ask(self, question: str, session_id: str | None) -> dict[str, Any]:
        return self._request(
            "POST",
            "/chat/ask",
            json={"question": question, "session_id": session_id},
            timeout=SLOW_TIMEOUT_SECONDS,
        )

    def agent_chat(self, message: str, session_id: str | None) -> dict[str, Any]:
        return self._request(
            "POST",
            "/agent/chat",
            json={"message": message, "session_id": session_id},
            timeout=SLOW_TIMEOUT_SECONDS,
        )

    def _request(
        self, method: str, path: str, *, timeout: float | None = None, **kwargs: Any
    ) -> Any:
        url = f"{self._base_url}{path}"
        try:
            # One client per request, as `httpx.request` does: Streamlit reruns
            # the script often, so there is no long-lived owner to close a pool.
            with httpx.Client(transport=self._transport) as http:
                response = http.request(
                    method, url, timeout=timeout or self._timeout, **kwargs
                )
        except httpx.ConnectError as error:
            raise ApiError(
                f"Can't reach the API at {self._base_url}; "
                "start it with `uv run pka-api`."
            ) from error
        except httpx.TimeoutException as error:
            raise ApiError(
                f"The API at {self._base_url} took too long to answer."
            ) from error
        except httpx.HTTPError as error:
            raise ApiError(f"Request to {url} failed: {error}") from error

        if response.is_error:
            raise ApiError(_error_message(response), response.status_code)
        try:
            return response.json()
        except ValueError as error:  # e.g. a proxy's HTML page instead of the API
            raise ApiError(
                f"The API at {self._base_url} returned a non-JSON response.",
                response.status_code,
            ) from error


def _error_message(response: httpx.Response) -> str:
    """The API's `detail`, including FastAPI's list form for validation errors."""
    try:
        body = response.json()
    except ValueError:
        body = None
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, str) and detail:
        return detail
    if isinstance(detail, list) and detail:
        return "; ".join(_validation_message(item) for item in detail)
    return f"The API returned HTTP {response.status_code}."


def _validation_message(item: Any) -> str:
    if not isinstance(item, dict):
        return str(item)
    field = ".".join(str(part) for part in item.get("loc", []) if part != "body")
    message = str(item.get("msg", "invalid value"))
    return f"{field}: {message}" if field else message
