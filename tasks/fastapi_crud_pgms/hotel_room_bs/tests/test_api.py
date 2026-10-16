"""API tests for the Hotel Room Booking System. No MySQL required.

The real repository is swapped for an in-memory one via dependency
overrides. TestClient is used *without* a `with` block on purpose: that
skips the app lifespan, which would otherwise connect to MySQL to create
the table.
"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from lifecycle import ACTIVE_STATUSES, StatusTransition
from main import app
from repository import get_repository
from schemas import Booking, BookingCreate, BookingStatus, RoomUpdate


class InMemoryBookingRepository:
    """Same public methods as BookingRepository, backed by a dict."""

    def __init__(self) -> None:
        self._bookings: dict[int, Booking] = {}
        self._next_id = 1

    def add(self, booking: BookingCreate) -> Booking:
        stored = Booking(
            id=self._next_id, status=BookingStatus.BOOKED, **booking.model_dump()
        )
        self._bookings[stored.id] = stored
        self._next_id += 1
        return stored

    def list_page(self, limit: int, offset: int) -> list[Booking]:
        ordered = sorted(self._bookings.values(), key=lambda b: b.id)
        return ordered[offset : offset + limit]

    def count(self) -> int:
        return len(self._bookings)

    def get(self, booking_id: int) -> Booking | None:
        return self._bookings.get(booking_id)

    def update_room(self, booking_id: int, changes: RoomUpdate) -> bool:
        current = self._bookings.get(booking_id)
        if current is None or current.status not in ACTIVE_STATUSES:
            return False
        updated = current.model_copy(update=changes.model_dump(exclude_none=True))
        self._bookings[booking_id] = updated
        # Mirrors MySQL's rowcount: unchanged values don't count as a change.
        return updated != current

    def change_status(self, booking_id: int, transition: StatusTransition) -> bool:
        current = self._bookings.get(booking_id)
        if current is None or current.status != transition.from_status:
            return False
        self._bookings[booking_id] = current.model_copy(
            update={"status": transition.to_status}
        )
        return True

    def delete_all(self) -> int:
        deleted = len(self._bookings)
        self._bookings.clear()
        return deleted


@pytest.fixture
def client() -> Iterator[TestClient]:
    repository = InMemoryBookingRepository()
    app.dependency_overrides[get_repository] = lambda: repository
    yield TestClient(app)
    app.dependency_overrides.clear()


VALID_BOOKING = {
    "customer_name": "Asha Rao",
    "room_number": 101,
    "room_type": "Deluxe",
    "amount": 4500.5,
}


def make_booking(client: TestClient, **overrides: object) -> dict:
    response = client.post("/bookings", json={**VALID_BOOKING, **overrides})
    assert response.status_code == 201, response.text
    return response.json()


def booking_in_status(client: TestClient, status: str) -> int:
    """Create a booking and drive it to `status`; return its id."""
    booking_id = make_booking(client)["id"]
    actions = {
        "Booked": [],
        "Checked In": ["check-in"],
        "Checked Out": ["check-in", "check-out"],
        "Cancelled": ["cancel"],
    }[status]
    for action in actions:
        assert client.post(f"/bookings/{booking_id}/{action}").status_code == 200
    return booking_id


# ---------- Web UI ----------

def test_root_serves_the_web_ui(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "<title>Hotel Room Booking</title>" in response.text


# ---------- Create / read ----------

def test_create_booking_returns_it_with_an_id_and_booked_status(
    client: TestClient,
) -> None:
    created = make_booking(client)

    assert created == {"id": 1, "status": "Booked", **VALID_BOOKING}
    assert client.get("/bookings/1").json() == created


def test_create_booking_strips_surrounding_whitespace(client: TestClient) -> None:
    created = make_booking(client, customer_name="  Ravi Kumar  ")

    assert created["customer_name"] == "Ravi Kumar"


def test_create_booking_ignores_a_client_supplied_status(client: TestClient) -> None:
    created = make_booking(client, status="Checked Out")

    assert created["status"] == "Booked"


@pytest.mark.parametrize(
    "overrides",
    [
        {"customer_name": "   "},
        {"customer_name": "x" * 101},
        {"room_number": 0},
        {"room_number": "abc"},
        {"room_type": "Penthouse"},
        {"amount": 0},
        {"amount": -10},
        {"amount": 100_000_000},
    ],
)
def test_create_booking_rejects_invalid_fields(
    client: TestClient, overrides: dict
) -> None:
    payload = {**VALID_BOOKING, **overrides}

    assert client.post("/bookings", json=payload).status_code == 422


@pytest.mark.parametrize("missing", list(VALID_BOOKING))
def test_create_booking_requires_every_field(client: TestClient, missing: str) -> None:
    payload = {k: v for k, v in VALID_BOOKING.items() if k != missing}

    assert client.post("/bookings", json=payload).status_code == 422


def test_get_missing_booking_returns_404(client: TestClient) -> None:
    response = client.get("/bookings/99")

    assert response.status_code == 404
    assert response.json()["detail"] == "Booking 99 not found."


# ---------- List / pagination ----------

def test_list_is_empty_initially(client: TestClient) -> None:
    assert client.get("/bookings").json() == {
        "items": [], "total": 0, "limit": 10, "offset": 0,
    }


def test_list_defaults_to_first_ten_sorted_by_id(client: TestClient) -> None:
    for i in range(12):
        make_booking(client, room_number=100 + i)

    page = client.get("/bookings").json()

    assert [b["id"] for b in page["items"]] == list(range(1, 11))
    assert (page["total"], page["limit"], page["offset"]) == (12, 10, 0)


def test_list_offset_and_limit_select_a_page(client: TestClient) -> None:
    for i in range(5):
        make_booking(client, room_number=100 + i)

    page = client.get("/bookings", params={"limit": 2, "offset": 3}).json()

    assert [b["id"] for b in page["items"]] == [4, 5]
    assert page["total"] == 5


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 101}, {"offset": -1}])
def test_list_rejects_out_of_range_paging(client: TestClient, params: dict) -> None:
    assert client.get("/bookings", params=params).status_code == 422


# ---------- Update room ----------

@pytest.mark.parametrize(
    ("changes", "expected_number", "expected_type"),
    [
        ({"room_number": 205}, 205, "Deluxe"),
        ({"room_type": "Suite"}, 101, "Suite"),
        ({"room_number": 310, "room_type": "Single"}, 310, "Single"),
    ],
)
def test_update_room_changes_only_given_fields(
    client: TestClient, changes: dict, expected_number: int, expected_type: str
) -> None:
    make_booking(client)

    response = client.patch("/bookings/1/room", json=changes)

    assert response.status_code == 200
    updated = response.json()
    assert (updated["room_number"], updated["room_type"]) == (
        expected_number, expected_type,
    )
    assert (updated["customer_name"], updated["amount"], updated["status"]) == (
        "Asha Rao", 4500.5, "Booked",
    )
    assert client.get("/bookings/1").json() == updated


def test_update_room_is_allowed_while_checked_in(client: TestClient) -> None:
    booking_id = booking_in_status(client, "Checked In")

    response = client.patch(f"/bookings/{booking_id}/room", json={"room_number": 404})

    assert response.status_code == 200
    assert response.json()["room_number"] == 404
    assert response.json()["status"] == "Checked In"


def test_update_room_with_unchanged_values_succeeds(client: TestClient) -> None:
    created = make_booking(client)

    response = client.patch("/bookings/1/room", json={"room_number": 101})

    assert response.status_code == 200
    assert response.json() == created


@pytest.mark.parametrize("status", ["Checked Out", "Cancelled"])
def test_update_room_on_finished_booking_returns_409(
    client: TestClient, status: str
) -> None:
    booking_id = booking_in_status(client, status)

    response = client.patch(f"/bookings/{booking_id}/room", json={"room_number": 999})

    assert response.status_code == 409
    assert response.json()["detail"] == f"Cannot change the room: booking is {status}."
    assert client.get(f"/bookings/{booking_id}").json()["room_number"] == 101


@pytest.mark.parametrize(
    "changes", [{}, {"room_number": 0}, {"room_type": "Penthouse"}]
)
def test_update_room_rejects_invalid_body(client: TestClient, changes: dict) -> None:
    make_booking(client)

    assert client.patch("/bookings/1/room", json=changes).status_code == 422


def test_update_room_on_missing_booking_returns_404(client: TestClient) -> None:
    assert client.patch("/bookings/99/room", json={"room_number": 5}).status_code == 404


# ---------- Check in / check out / cancel ----------

def test_full_stay_moves_booked_to_checked_in_to_checked_out(
    client: TestClient,
) -> None:
    make_booking(client)

    checked_in = client.post("/bookings/1/check-in")
    assert checked_in.status_code == 200
    assert checked_in.json()["status"] == "Checked In"

    checked_out = client.post("/bookings/1/check-out")
    assert checked_out.status_code == 200
    assert checked_out.json()["status"] == "Checked Out"

    assert client.get("/bookings/1").json()["status"] == "Checked Out"


def test_cancel_keeps_the_booking_as_cancelled(client: TestClient) -> None:
    make_booking(client)

    response = client.post("/bookings/1/cancel")

    assert response.status_code == 200
    assert response.json()["status"] == "Cancelled"
    assert client.get("/bookings").json()["total"] == 1


@pytest.mark.parametrize(
    ("status", "action", "expected_detail"),
    [
        ("Checked In", "check-in", "Booking is already Checked In."),
        ("Checked Out", "check-in", "Cannot check in: booking is Checked Out."),
        ("Cancelled", "check-in", "Cannot check in: booking is Cancelled."),
        ("Booked", "check-out", "Cannot check out: the guest has not checked in yet."),
        ("Checked Out", "check-out", "Booking is already Checked Out."),
        ("Cancelled", "check-out", "Cannot check out: booking is Cancelled."),
        ("Checked In", "cancel", "Cannot cancel: booking is Checked In."),
        ("Checked Out", "cancel", "Cannot cancel: booking is Checked Out."),
        ("Cancelled", "cancel", "Booking is already Cancelled."),
    ],
)
def test_disallowed_status_change_returns_409_and_keeps_status(
    client: TestClient, status: str, action: str, expected_detail: str
) -> None:
    booking_id = booking_in_status(client, status)

    response = client.post(f"/bookings/{booking_id}/{action}")

    assert response.status_code == 409
    assert response.json()["detail"] == expected_detail
    assert client.get(f"/bookings/{booking_id}").json()["status"] == status


@pytest.mark.parametrize("action", ["check-in", "check-out", "cancel"])
def test_status_change_on_missing_booking_returns_404(
    client: TestClient, action: str
) -> None:
    assert client.post(f"/bookings/99/{action}").status_code == 404


# ---------- Delete all ----------

def test_delete_all_requires_confirmation(client: TestClient) -> None:
    make_booking(client)

    assert client.delete("/bookings").status_code == 400
    assert client.get("/bookings").json()["total"] == 1


def test_delete_all_with_confirmation_empties_the_table(client: TestClient) -> None:
    make_booking(client)
    booking_in_status(client, "Checked Out")

    response = client.delete("/bookings", params={"confirm": "true"})

    assert response.status_code == 200
    assert response.json() == {"deleted": 2}
    assert client.get("/bookings").json()["total"] == 0
