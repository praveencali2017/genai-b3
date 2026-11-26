import threading

import pytest
from sqlalchemy.orm import Session, sessionmaker

from ai_travel_agent.config.database import session_scope
from ai_travel_agent.exceptions.errors import InsufficientCapacityError
from ai_travel_agent.models import Package
from ai_travel_agent.repositories.user_repository import UserRepository
from ai_travel_agent.schemas.booking import BookingCreate
from ai_travel_agent.services.booking_service import BookingService
from tests.api.conftest import count, seats

pytestmark = pytest.mark.mysql


def test_two_travellers_racing_for_the_last_seat_get_one_booking(
    session_factory: sessionmaker[Session], packages: dict[str, int]
) -> None:
    kashmir = packages["Kashmir Explorer"]
    with session_scope(session_factory) as session:
        package = session.get(Package, kashmir)
        assert package is not None
        package.capacity = 1

    start = threading.Barrier(2)
    outcomes: dict[str, str] = {}

    def attempt(username: str) -> None:
        request = BookingCreate.model_validate(
            {"lines": [{"package_id": kashmir, "quantity": 1}]}
        )
        start.wait()
        try:
            with session_scope(session_factory) as session:
                user = UserRepository(session).get_by_username(username)
                assert user is not None
                BookingService(session).place(user, request, f"race-{username}")
            outcomes[username] = "booked"
        except InsufficientCapacityError:
            outcomes[username] = "sold out"

    threads = [threading.Thread(target=attempt, args=(n,)) for n in ("alice", "bob")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert sorted(outcomes.values()) == ["booked", "sold out"]
    assert count(session_factory, "bookings") == 1
    assert seats(session_factory, kashmir) == 0
