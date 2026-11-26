import pytest
from fastapi.testclient import TestClient

from tests.api.conftest import error_body

pytestmark = pytest.mark.mysql


def test_health_reports_the_database(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok", "database": "ok"}


def test_protected_routes_need_valid_credentials(client: TestClient) -> None:
    missing = client.get("/api/bookings")
    assert missing.status_code == 401
    assert missing.headers["WWW-Authenticate"].startswith("Basic")
    assert error_body(missing)["code"] == "UNAUTHENTICATED"

    wrong = client.get("/api/bookings", auth=("alice", "wrong-password"))
    assert wrong.status_code == 401
    unknown = client.get("/api/auth/me", auth=("nobody", "whatever"))
    assert unknown.status_code == 401


def test_errors_carry_the_request_id_and_no_internals(client: TestClient) -> None:
    response = client.get("/api/packages/999999", headers={"X-Request-ID": "trace-42"})

    assert response.status_code == 404
    body = error_body(response)
    assert body["request_id"] == "trace-42"
    assert response.headers["X-Request-ID"] == "trace-42"
    assert "Traceback" not in response.text


def test_the_internal_agent_identity_cannot_be_forged(client: TestClient) -> None:
    response = client.get(
        "/api/bookings",
        headers={"X-Internal-Token": "guess", "X-Acting-User-Id": "1"},
    )
    assert response.status_code == 401
