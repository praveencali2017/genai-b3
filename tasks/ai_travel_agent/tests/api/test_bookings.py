from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from ai_travel_agent.config.database import session_scope
from ai_travel_agent.models import Package
from tests.api.conftest import ADMIN, ALICE, BOB, book, count, error_body, seats

pytestmark = pytest.mark.mysql


def test_booking_snapshots_the_price_and_takes_the_seats(
    client: TestClient,
    packages: dict[str, int],
    session_factory: sessionmaker[Session],
) -> None:
    kashmir = packages["Kashmir Explorer"]

    response = client.post(
        "/api/bookings",
        # The capstone contract's field name for the package.
        json={"lines": [{"item_id": kashmir, "quantity": 2}]},
        headers={"Idempotency-Key": "demo-001"},
        auth=ALICE,
    )

    assert response.status_code == 201
    booking = response.json()
    assert booking["status"] == "CONFIRMED"
    assert booking["total"] == "48000.00"
    assert seats(session_factory, kashmir) == 6

    # A later price change doesn't touch the booking.
    client.put(f"/api/packages/{kashmir}", json={"price": "30000"}, auth=ADMIN)
    again = client.get(f"/api/bookings/{booking['id']}", auth=ALICE).json()
    assert again["lines"][0]["unit_price"] == "24000.00"
    assert again["total"] == "48000.00"


def test_repeating_a_request_with_the_same_key_replays_it(
    client: TestClient,
    packages: dict[str, int],
    session_factory: sessionmaker[Session],
) -> None:
    kashmir = packages["Kashmir Explorer"]
    first = book(client, kashmir, 1, key="same-key")
    replay = book(client, kashmir, 1, key="same-key")

    assert first.status_code == 201
    assert replay.status_code == 200
    assert replay.headers["Idempotent-Replayed"] == "true"
    assert replay.json()["id"] == first.json()["id"]
    assert count(session_factory, "bookings") == 1
    assert seats(session_factory, kashmir) == 7

    conflict = book(client, kashmir, 2, key="same-key")
    assert conflict.status_code == 409
    assert error_body(conflict)["code"] == "IDEMPOTENCY_CONFLICT"


def test_insufficient_seats_fail_the_whole_booking(
    client: TestClient,
    packages: dict[str, int],
    session_factory: sessionmaker[Session],
) -> None:
    kashmir, goa = packages["Kashmir Explorer"], packages["Coastal Escape"]

    response = client.post(
        "/api/bookings",
        json={
            "lines": [
                {"package_id": goa, "quantity": 2},
                {"package_id": kashmir, "quantity": 9},
            ]
        },
        headers={"Idempotency-Key": "too-many"},
        auth=ALICE,
    )

    assert response.status_code == 409
    assert error_body(response)["code"] == "INSUFFICIENT_CAPACITY"
    assert count(session_factory, "bookings") == 0
    assert seats(session_factory, goa) == 12
    assert seats(session_factory, kashmir) == 8


def test_a_departed_package_cannot_be_booked(
    client: TestClient,
    packages: dict[str, int],
    session_factory: sessionmaker[Session],
) -> None:
    jaipur = packages["Jaipur Heritage Trail"]
    with session_scope(session_factory) as session:
        package = session.get(Package, jaipur)
        assert package is not None
        package.start_date = date.today()

    response = book(client, jaipur)

    assert response.status_code == 409
    assert error_body(response)["code"] == "DEPARTURE_PASSED"
    # And it has left the public catalog.
    titles = [p["title"] for p in client.get("/api/packages").json()]
    assert "Jaipur Heritage Trail" not in titles


@pytest.mark.parametrize(
    ("title", "percent"),
    [
        ("Coastal Escape", 100),  # departs in 14 days
        ("Rishikesh Rafting Weekend", 50),  # departs in 5 days
        ("Jaipur Heritage Trail", 0),  # departs tomorrow
    ],
)
def test_cancellation_refund_follows_the_policy_and_restores_seats_once(
    client: TestClient,
    packages: dict[str, int],
    session_factory: sessionmaker[Session],
    title: str,
    percent: int,
) -> None:
    package_id = packages[title]
    before = seats(session_factory, package_id)
    booking = book(client, package_id, 2).json()
    assert booking["cancellation_quote"]["refund_percent"] == percent

    cancelled = client.post(f"/api/bookings/{booking['id']}/cancel", auth=ALICE)

    assert cancelled.status_code == 200
    body = cancelled.json()
    assert body["status"] == "CANCELLED"
    expected = (Decimal(booking["total"]) * percent / 100).quantize(Decimal("0.01"))
    assert Decimal(body["refund_amount"]) == expected
    assert body["cancellation_quote"] is None
    assert seats(session_factory, package_id) == before

    twice = client.post(f"/api/bookings/{booking['id']}/cancel", auth=ALICE)
    assert twice.status_code == 409
    assert error_body(twice)["code"] == "INVALID_TRANSITION"
    assert seats(session_factory, package_id) == before


def test_other_users_bookings_are_invisible(
    client: TestClient, packages: dict[str, int]
) -> None:
    booking = book(client, packages["Kashmir Explorer"], auth=ALICE).json()

    for request in (
        lambda: client.get(f"/api/bookings/{booking['id']}", auth=BOB),
        lambda: client.post(f"/api/bookings/{booking['id']}/cancel", auth=BOB),
    ):
        response = request()
        assert response.status_code == 404
        assert error_body(response)["code"] == "NOT_FOUND"
    assert client.get("/api/bookings", auth=BOB).json() == []
    # Admins may read it.
    assert client.get(f"/api/bookings/{booking['id']}", auth=ADMIN).status_code == 200


def test_admin_status_transitions(
    client: TestClient,
    packages: dict[str, int],
    session_factory: sessionmaker[Session],
) -> None:
    goa = packages["Coastal Escape"]
    completed = book(client, goa, 1, key="k1").json()
    cancelled = book(client, goa, 3, key="k2").json()

    done = client.patch(
        f"/api/admin/bookings/{completed['id']}/status",
        json={"status": "COMPLETED"},
        auth=ADMIN,
    )
    assert done.json()["status"] == "COMPLETED"
    back = client.patch(
        f"/api/admin/bookings/{completed['id']}/status",
        json={"status": "CONFIRMED"},
        auth=ADMIN,
    )
    assert back.status_code == 409

    operator = client.patch(
        f"/api/admin/bookings/{cancelled['id']}/status",
        json={"status": "CANCELLED"},
        auth=ADMIN,
    ).json()
    assert operator["refund_amount"] == operator["total"]
    assert seats(session_factory, goa) == 12 - 1
