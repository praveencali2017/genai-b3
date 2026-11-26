"""A duck-typed stand-in for the agent's REST client, a canned retriever and a
recording `propose`, so tools and the agent loop run without a server."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import pytest

from ai_travel_agent.agents.api_client import ApiCallError
from ai_travel_agent.agents.tools import AgentToolkit, ProposedAction
from ai_travel_agent.models.enums import PendingActionType
from ai_travel_agent.rag.vector_store import ChunkHit

KASHMIR = {
    "id": 1,
    "title": "Kashmir Explorer",
    "destination": "Srinagar",
    "description": "Shikara ride on Dal Lake.",
    "duration_days": 5,
    "price": "24000.00",
    "capacity": 8,
    "start_date": "2026-12-20",
    "end_date": "2026-12-24",
    "active": True,
}

CONFIRMED_BOOKING = {
    "id": 501,
    "user_id": 7,
    "status": "CONFIRMED",
    "total": "24000.00",
    "refund_amount": None,
    "cancelled_at": None,
    "created_at": "2026-11-25T10:00:00",
    "updated_at": "2026-11-25T10:00:00",
    "lines": [
        {
            "package_id": 1,
            "title": "Kashmir Explorer",
            "destination": "Srinagar",
            "start_date": "2026-12-20",
            "quantity": 1,
            "unit_price": "24000.00",
            "line_total": "24000.00",
        }
    ],
    "cancellation_quote": {
        "refund_amount": "24000.00",
        "refund_percent": 100,
        "days_before_departure": 25,
    },
}


@dataclass
class StubApi:
    """Records every call; has no write methods the toolkit could reach."""

    packages: dict[int, dict[str, Any]] = field(default_factory=lambda: {1: KASHMIR})
    bookings: dict[int, dict[str, Any]] = field(
        default_factory=lambda: {501: CONFIRMED_BOOKING}
    )
    calls: list[tuple[str, Any]] = field(default_factory=list)

    def search_packages(self, filters: dict[str, Any]) -> list[dict[str, Any]]:
        self.calls.append(("search_packages", filters))
        return list(self.packages.values())

    def get_package(self, package_id: int) -> dict[str, Any]:
        self.calls.append(("get_package", package_id))
        if package_id not in self.packages:
            raise ApiCallError(404, "NOT_FOUND", f"Package {package_id} not found.")
        return self.packages[package_id]

    def get_booking(self, booking_id: int) -> dict[str, Any]:
        self.calls.append(("get_booking", booking_id))
        if booking_id not in self.bookings:
            raise ApiCallError(404, "NOT_FOUND", f"Booking {booking_id} not found.")
        return self.bookings[booking_id]

    def create_booking(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("tools must never create a booking directly")

    def cancel_booking(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("tools must never cancel a booking directly")


@dataclass
class StubRetriever:
    hits: list[ChunkHit] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)

    def retrieve(self, question: str) -> list[ChunkHit]:
        self.questions.append(question)
        return self.hits


@dataclass
class RecordingProposals:
    proposals: list[tuple[PendingActionType, dict[str, Any]]] = field(
        default_factory=list
    )

    def __call__(
        self, action_type: PendingActionType, terms: dict[str, Any]
    ) -> ProposedAction:
        self.proposals.append((action_type, terms))
        return ProposedAction(len(self.proposals), datetime(2026, 11, 25, 12, 0))


def policy_hit(text: str, *, filename: str = "Cancellation_Policy.pdf") -> ChunkHit:
    return ChunkHit(
        document_id=1,
        filename=filename,
        version=1,
        page=1,
        chunk_index=0,
        text=text,
        score=0.9,
    )


@pytest.fixture
def api() -> StubApi:
    return StubApi()


@pytest.fixture
def retriever() -> StubRetriever:
    return StubRetriever()


@pytest.fixture
def proposals() -> RecordingProposals:
    return RecordingProposals()


@pytest.fixture
def toolkit(
    api: StubApi, retriever: StubRetriever, proposals: RecordingProposals
) -> AgentToolkit:
    return AgentToolkit(api=api, retriever=retriever, propose=proposals)  # type: ignore[arg-type]
