from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import ADMIN, ALICE, count, error_body

pytestmark = pytest.mark.mysql


def _new_package(**overrides: object) -> dict[str, object]:
    package: dict[str, object] = {
        "title": "Spiti Valley Circuit",
        "destination": "Kaza",
        "duration_days": 6,
        "price": "29500",
        "capacity": 10,
        "start_date": (date.today() + timedelta(days=50)).isoformat(),
    }
    return {**package, **overrides}


def test_anyone_can_browse_and_filter_the_catalog(
    client: TestClient, packages: dict[str, int]
) -> None:
    everything = client.get("/api/packages").json()
    assert len(everything) == len(packages)

    kashmir = client.get(
        "/api/packages",
        params={"destination": "srinagar", "max_price": 25000, "duration_days": 5},
    ).json()
    assert [p["title"] for p in kashmir] == ["Kashmir Explorer"]
    assert kashmir[0]["price"] == "24000.00"
    assert kashmir[0]["capacity"] == 8

    # A region name finds packages whose destination is a city in it.
    by_region = client.get(
        "/api/packages", params={"destination": "Kashmir", "max_price": 25000}
    ).json()
    assert [p["title"] for p in by_region] == ["Kashmir Explorer"]

    detail = client.get(f"/api/packages/{packages['Coastal Escape']}").json()
    assert detail["destination"] == "Goa"
    assert detail["end_date"] > detail["start_date"]


def test_admin_crud_and_soft_delete(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    created = client.post("/api/packages", json=_new_package(), auth=ADMIN)
    assert created.status_code == 201
    package_id = created.json()["id"]

    updated = client.put(
        f"/api/packages/{package_id}", json={"price": "27500.50"}, auth=ADMIN
    )
    assert updated.status_code == 200
    assert updated.json()["price"] == "27500.50"

    assert client.delete(f"/api/packages/{package_id}", auth=ADMIN).status_code == 204
    assert client.get(f"/api/packages/{package_id}").status_code == 404
    listed = client.get("/api/packages", params={"keyword": "Spiti"}).json()
    assert listed == []
    # Soft delete: the row is still there for admins.
    admin_view = client.get(
        "/api/packages", params={"include_inactive": True}, auth=ADMIN
    ).json()
    assert any(p["id"] == package_id and not p["active"] for p in admin_view)
    assert count(session_factory, "packages") == 13


@pytest.mark.parametrize(
    "overrides",
    [
        {"price": "0"},
        {"price": "-100"},
        {"capacity": -1},
        {"title": "ab"},
        {"start_date": (date.today() - timedelta(days=1)).isoformat()},
    ],
)
def test_invalid_packages_are_rejected_without_a_database_change(
    client: TestClient,
    session_factory: sessionmaker[Session],
    overrides: dict[str, object],
) -> None:
    before = count(session_factory, "packages")

    response = client.post("/api/packages", json=_new_package(**overrides), auth=ADMIN)

    assert response.status_code == 422
    body = error_body(response)
    assert body["code"] == "VALIDATION_ERROR"
    assert count(session_factory, "packages") == before


def test_a_user_cannot_use_admin_routes(
    client: TestClient,
    session_factory: sessionmaker[Session],
    packages: dict[str, int],
) -> None:
    created = client.post("/api/packages", json=_new_package(), auth=ALICE)
    assert created.status_code == 403
    assert error_body(created)["code"] == "FORBIDDEN"

    changed = client.put(
        f"/api/packages/{packages['Kashmir Explorer']}",
        json={"price": "1"},
        auth=ALICE,
    )
    assert changed.status_code == 403
    assert client.get("/api/admin/bookings", auth=ALICE).status_code == 403
    assert client.get("/api/packages?include_inactive=true").status_code == 403
    assert count(session_factory, "packages") == len(packages)
