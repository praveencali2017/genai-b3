"""The agent's HTTP client for this application's own REST API.

The agent reads live data only through the same endpoints the browser uses, so
every validation, role and ownership rule applies to it unchanged. The base URL
is fixed configuration, paths are built here from validated integer ids, and
the identity headers come from the server-side session; nothing the model
writes can choose a host, a path, a role or a credential.
"""

from typing import Any

import httpx

from ai_travel_agent.security.internal_identity import (
    ACTING_USER_HEADER,
    INTERNAL_TOKEN_HEADER,
)


class ApiCallError(Exception):
    """The API refused the call or couldn't be reached."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


class TravelApiClient:
    def __init__(
        self,
        base_url: str,
        internal_token: str,
        acting_user_id: int,
        timeout: float,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._http = httpx.Client(
            base_url=base_url,
            timeout=timeout,
            transport=transport,
            headers={
                "Accept": "application/json",
                INTERNAL_TOKEN_HEADER: internal_token,
                ACTING_USER_HEADER: str(acting_user_id),
            },
            # The internal token must never follow a redirect elsewhere.
            follow_redirects=False,
        )

    def close(self) -> None:
        self._http.close()

    def search_packages(self, filters: dict[str, Any]) -> list[dict[str, Any]]:
        params = {k: _query_value(v) for k, v in filters.items() if v is not None}
        return self._get("/api/packages", params)

    def get_package(self, package_id: int) -> dict[str, Any]:
        return self._get(f"/api/packages/{int(package_id)}")

    def get_booking(self, booking_id: int) -> dict[str, Any]:
        return self._get(f"/api/bookings/{int(booking_id)}")

    def list_bookings(self) -> list[dict[str, Any]]:
        return self._get("/api/bookings")

    # The two writes below are used only by PendingActionService after the
    # traveller pressed Confirm; they are never offered to the model as tools.

    def create_booking(
        self, lines: list[dict[str, int]], idempotency_key: str
    ) -> dict[str, Any]:
        return self._send(
            "POST",
            "/api/bookings",
            json={"lines": lines},
            headers={"Idempotency-Key": idempotency_key},
        )

    def cancel_booking(self, booking_id: int) -> dict[str, Any]:
        return self._send("POST", f"/api/bookings/{int(booking_id)}/cancel")

    def _get(self, path: str, params: dict[str, str] | None = None) -> Any:
        return self._send("GET", path, params=params)

    def _send(self, method: str, path: str, **options: Any) -> Any:
        try:
            response = self._http.request(method, path, **options)
        except httpx.HTTPError as error:
            raise ApiCallError(
                0, "API_UNREACHABLE", f"The travel API is unreachable: {error}"
            ) from error
        if response.is_success:
            return response.json()
        try:
            body = response.json()
        except ValueError:
            body = {}
        raise ApiCallError(
            response.status_code,
            str(body.get("code", f"HTTP_{response.status_code}")),
            str(body.get("message", response.reason_phrase)),
        )


def _query_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)
