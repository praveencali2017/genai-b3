"""The chatbot end to end: persistence, the three answer paths, the
confirmation gate, idempotent execution, failures and restart continuity.

The language model is scripted (`FakeChatClient`); everything else is real:
the agent's tools call the REST API through HTTP with the user's identity,
retrieval runs over Chroma, and every row lands in MySQL.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text, update
from sqlalchemy.orm import Session, sessionmaker

from ai_travel_agent.config.database import session_scope
from ai_travel_agent.models import PendingAction
from ai_travel_agent.models.base import utc_now
from tests.api.conftest import ADMIN, ALICE, BOB, RunningApp, count, seats
from tests.fakes import (
    FakeChatClient,
    FakeEmbeddingClient,
    openai_timeout,
    text_completion,
    tool_completion,
)

pytestmark = pytest.mark.mysql

KNOWLEDGE_BASE = Path(__file__).resolve().parents[2] / "knowledge_base"


def _upload(client: TestClient, filename: str, content: bytes | None = None):
    data = content if content is not None else (KNOWLEDGE_BASE / filename).read_bytes()
    return client.post("/api/documents", files={"file": (filename, data)}, auth=ADMIN)


def _session(client: TestClient, auth: tuple[str, str] = ALICE) -> int:
    response = client.post("/api/chat/sessions", auth=auth)
    assert response.status_code == 201
    return response.json()["id"]


def _say(client: TestClient, session_id: int, content: str, auth=ALICE):
    return client.post(
        f"/api/chat/sessions/{session_id}/messages",
        json={"content": content},
        auth=auth,
    )


def _decide(client: TestClient, action_id: int, decision: str, auth=ALICE):
    return client.post(
        f"/api/chat/actions/{action_id}/confirm",
        json={"decision": decision},
        auth=auth,
    )


def _script_booking_turn(fake_chat: FakeChatClient, package_id: int) -> None:
    fake_chat.script(
        tool_completion(
            ("retrieve_policy", {"question": "What is the cancellation policy?"}),
            (
                "search_packages",
                {"keyword": "Kashmir", "max_price": 25000, "duration_days": 5},
            ),
        ),
        tool_completion(
            (
                "create_booking",
                {
                    "lines": [
                        {
                            "package_id": package_id,
                            "title": "Kashmir Explorer",
                            "quantity": 1,
                        }
                    ]
                },
            )
        ),
        text_completion(
            "Kashmir Explorer departs soon for ₹24000. Cancelling at least 7 days "
            "before departure is refunded in full [Cancellation_Policy.pdf p.1]. "
            "Press Confirm to book."
        ),
    )


def test_a_general_question_is_answered_and_persisted(
    client: TestClient, fake_chat: FakeChatClient
) -> None:
    session_id = _session(client)
    fake_chat.script(text_completion("Day 1: Srinagar houseboat..."))

    turn = _say(client, session_id, "Suggest a five-day Kashmir itinerary.").json()

    assert turn["assistant_message"]["mode"] == "GENERAL"
    assert turn["assistant_message"]["pending_actions"] == []
    history = client.get(f"/api/chat/sessions/{session_id}/messages", auth=ALICE)
    assert [(m["seq_no"], m["role"]) for m in history.json()] == [
        (1, "user"),
        (2, "assistant"),
    ]
    sessions = client.get("/api/chat/sessions", auth=ALICE).json()
    assert sessions[0]["title"] == "Suggest a five-day Kashmir itinerary."


def test_a_policy_question_is_grounded_with_a_page_citation(
    client: TestClient, fake_chat: FakeChatClient
) -> None:
    assert _upload(client, "Cancellation_Policy.pdf").status_code == 201
    session_id = _session(client)
    fake_chat.script(
        tool_completion(("retrieve_policy", {"question": "cancellation refund days"})),
        text_completion(
            "Cancel at least 7 days before departure for a full refund "
            "[Cancellation_Policy.pdf p.1]."
        ),
    )

    reply = _say(client, session_id, "What is the cancellation policy?").json()[
        "assistant_message"
    ]

    assert reply["mode"] == "RAG"
    [citation] = reply["citations"]
    assert citation["filename"] == "Cancellation_Policy.pdf"
    assert citation["page"] == 1
    assert citation["version"] == 1
    # The excerpt the model saw came from the indexed document.
    tool_message = next(
        m for m in fake_chat.calls[1]["messages"] if m["role"] == "tool"
    )
    assert "7 days before departure" in tool_message["content"]


def test_mixed_prompt_proposes_then_confirm_books_exactly_once(
    client: TestClient,
    fake_chat: FakeChatClient,
    packages: dict[str, int],
    session_factory: sessionmaker[Session],
) -> None:
    kashmir = packages["Kashmir Explorer"]
    _upload(client, "Cancellation_Policy.pdf")
    session_id = _session(client)
    _script_booking_turn(fake_chat, kashmir)

    reply = _say(
        client,
        session_id,
        "Find a five-day Kashmir package below INR 25000, explain cancellation "
        "and book it.",
    ).json()["assistant_message"]

    assert reply["mode"] == "MIXED"
    assert [c["filename"] for c in reply["citations"]] == ["Cancellation_Policy.pdf"]
    [action] = reply["pending_actions"]
    assert action["status"] == "PENDING"
    assert action["terms"]["total"] == "24000.00"
    # No write before confirmation.
    assert count(session_factory, "bookings") == 0
    assert seats(session_factory, kashmir) == 8
    assert count(session_factory, "tool_runs") == 3

    confirmed = _decide(client, action["id"], "CONFIRM")
    assert confirmed.status_code == 200
    result = confirmed.json()
    assert result["action"]["status"] == "CONFIRMED"
    booking_id = result["action"]["result"]["booking_id"]
    assert f"Booking #{booking_id}" in result["message"]["content"]

    again = _decide(client, action["id"], "CONFIRM").json()
    assert again["action"]["result"]["booking_id"] == booking_id
    assert again["message"]["id"] == result["message"]["id"]
    assert count(session_factory, "bookings") == 1
    assert seats(session_factory, kashmir) == 7

    booking = client.get(f"/api/bookings/{booking_id}", auth=ALICE).json()
    assert booking["status"] == "CONFIRMED"
    # The same booking is visible exactly like a manual one.
    assert [b["id"] for b in client.get("/api/bookings", auth=ALICE).json()] == [
        booking_id
    ]


def test_rejected_or_expired_proposals_change_nothing(
    client: TestClient,
    fake_chat: FakeChatClient,
    packages: dict[str, int],
    session_factory: sessionmaker[Session],
) -> None:
    session_id = _session(client)
    _script_booking_turn(fake_chat, packages["Kashmir Explorer"])
    first = _say(client, session_id, "book kashmir").json()["assistant_message"]
    _script_booking_turn(fake_chat, packages["Kashmir Explorer"])
    second = _say(client, session_id, "book kashmir").json()["assistant_message"]

    rejected = _decide(client, first["pending_actions"][0]["id"], "REJECT").json()
    assert rejected["action"]["status"] == "REJECTED"
    # A rejected proposal stays rejected.
    late = _decide(client, first["pending_actions"][0]["id"], "CONFIRM").json()
    assert late["action"]["status"] == "REJECTED"

    expired_id = second["pending_actions"][0]["id"]
    with session_scope(session_factory) as session:
        session.execute(
            update(PendingAction)
            .where(PendingAction.id == expired_id)
            .values(expires_at=utc_now())
        )
    expired = _decide(client, expired_id, "CONFIRM").json()
    assert expired["action"]["status"] == "EXPIRED"

    assert count(session_factory, "bookings") == 0


def test_a_price_change_before_confirmation_fails_the_proposal(
    client: TestClient,
    fake_chat: FakeChatClient,
    packages: dict[str, int],
    session_factory: sessionmaker[Session],
) -> None:
    kashmir = packages["Kashmir Explorer"]
    session_id = _session(client)
    _script_booking_turn(fake_chat, kashmir)
    action = _say(client, session_id, "book it").json()["assistant_message"][
        "pending_actions"
    ][0]
    client.put(f"/api/packages/{kashmir}", json={"price": "26000"}, auth=ADMIN)

    result = _decide(client, action["id"], "CONFIRM").json()

    assert result["action"]["status"] == "FAILED"
    assert result["message"]["error"]["code"] == "TERMS_CHANGED"
    assert count(session_factory, "bookings") == 0


def test_cancellation_through_chat_shows_the_refund_and_needs_confirmation(
    client: TestClient,
    fake_chat: FakeChatClient,
    packages: dict[str, int],
    session_factory: sessionmaker[Session],
) -> None:
    rishikesh = packages["Rishikesh Rafting Weekend"]
    booking = client.post(
        "/api/bookings",
        json={"lines": [{"package_id": rishikesh, "quantity": 2}]},
        headers={"Idempotency-Key": "manual-1"},
        auth=ALICE,
    ).json()
    session_id = _session(client)
    fake_chat.script(
        tool_completion(("cancel_booking", {"booking_id": booking["id"]})),
        text_completion("Cancelling refunds ₹7500 (50%). Press Confirm."),
    )

    action = _say(client, session_id, f"Cancel booking {booking['id']}").json()[
        "assistant_message"
    ]["pending_actions"][0]

    assert action["action"] == "CANCEL_BOOKING"
    assert action["terms"]["refund_percent"] == 50
    assert action["terms"]["refund_amount"] == "7500.00"
    assert client.get(f"/api/bookings/{booking['id']}", auth=ALICE).json()[
        "status"
    ] == ("CONFIRMED")

    result = _decide(client, action["id"], "CONFIRM").json()
    assert result["action"]["status"] == "CONFIRMED"
    assert result["action"]["result"]["refund_amount"] == "7500.00"
    assert seats(session_factory, rishikesh) == 15


def test_tools_act_only_as_the_signed_in_user(
    client: TestClient, fake_chat: FakeChatClient, packages: dict[str, int]
) -> None:
    alices = client.post(
        "/api/bookings",
        json={"lines": [{"package_id": packages["Coastal Escape"], "quantity": 1}]},
        headers={"Idempotency-Key": "alice-1"},
        auth=ALICE,
    ).json()
    bob_session = _session(client, BOB)
    fake_chat.script(
        tool_completion(("cancel_booking", {"booking_id": alices["id"]})),
        text_completion("I couldn't find that booking."),
    )

    reply = _say(client, bob_session, "cancel booking", auth=BOB).json()[
        "assistant_message"
    ]

    assert reply["pending_actions"] == []
    tool_result = next(m for m in fake_chat.calls[1]["messages"] if m["role"] == "tool")
    assert "NOT_FOUND" in tool_result["content"]
    # Bob can't read Alice's chat either.
    alice_session = _session(client)
    assert (
        client.get(f"/api/chat/sessions/{alice_session}/messages", auth=BOB).status_code
        == 404
    )


def test_an_ai_outage_returns_503_and_is_recorded(
    client: TestClient,
    fake_chat: FakeChatClient,
    session_factory: sessionmaker[Session],
) -> None:
    session_id = _session(client)
    fake_chat.script(openai_timeout())

    response = _say(client, session_id, "Find me a beach trip")

    assert response.status_code == 503
    assert response.json()["code"] == "AI_UNAVAILABLE"
    history = client.get(f"/api/chat/sessions/{session_id}/messages", auth=ALICE)
    user, failed = history.json()
    assert user["content"] == "Find me a beach trip"
    assert failed["error"]["code"] == "AI_UNAVAILABLE"
    assert count(session_factory, "bookings") == 0


def test_a_vector_store_failure_is_a_safe_error_too(
    client: TestClient,
    fake_chat: FakeChatClient,
    fake_embeddings: FakeEmbeddingClient,
) -> None:
    _upload(client, "Cancellation_Policy.pdf")
    session_id = _session(client)
    fake_embeddings.embeddings.failure = openai_timeout()
    fake_chat.script(
        tool_completion(("retrieve_policy", {"question": "refunds"})),
        text_completion("should never be reached"),
    )

    response = _say(client, session_id, "What is the refund policy?")

    assert response.status_code == 503
    assert fake_chat.unused_replies == 1


def test_history_bookings_and_pending_actions_survive_a_restart(
    running_app: RunningApp,
    fake_chat: FakeChatClient,
    packages: dict[str, int],
    session_factory: sessionmaker[Session],
) -> None:
    with running_app() as client:
        session_id = _session(client)
        _script_booking_turn(fake_chat, packages["Kashmir Explorer"])
        action_id = _say(client, session_id, "book kashmir").json()[
            "assistant_message"
        ]["pending_actions"][0]["id"]

    # A new process: new container, new internal token, nothing in memory.
    with running_app() as client:
        history = client.get(
            f"/api/chat/sessions/{session_id}/messages", auth=ALICE
        ).json()
        assert [m["seq_no"] for m in history] == [1, 2]
        assert history[1]["pending_actions"][0]["status"] == "PENDING"

        result = _decide(client, action_id, "CONFIRM").json()
        booking_id = result["action"]["result"]["booking_id"]

    with running_app() as client:
        booking = client.get(f"/api/bookings/{booking_id}", auth=ALICE).json()
        assert booking["status"] == "CONFIRMED"
        history = client.get(
            f"/api/chat/sessions/{session_id}/messages", auth=ALICE
        ).json()
        assert [m["seq_no"] for m in history] == [1, 2, 3]
    with session_scope(session_factory) as session:
        statuses = session.execute(text("SELECT status FROM tool_runs")).scalars()
        assert set(statuses) == {"SUCCESS"}


def test_a_new_document_version_replaces_the_old_one_in_retrieval(
    client: TestClient, fake_chat: FakeChatClient
) -> None:
    old = _upload(client, "Policy_Note.txt", b"Version 1.0. Pets are not allowed.")
    new = _upload(
        client, "Policy_Note.txt", b"Version 2.0. Small pets are allowed on trips."
    )
    assert old.json()["version"] == 1
    assert new.json()["version"] == 2
    duplicate = _upload(
        client, "Copy.txt", b"Version 2.0. Small pets are allowed on trips."
    )
    assert duplicate.status_code == 409

    listed = {
        d["version"]: d["status"]
        for d in client.get("/api/documents", auth=ADMIN).json()
    }
    assert listed == {1: "SUPERSEDED", 2: "ACTIVE"}

    session_id = _session(client)
    fake_chat.script(
        tool_completion(("retrieve_policy", {"question": "are pets allowed"})),
        text_completion("Small pets are allowed [Policy_Note.txt]."),
    )
    _say(client, session_id, "Can I bring my pet?")
    tool_message = next(
        m for m in fake_chat.calls[1]["messages"] if m["role"] == "tool"
    )
    assert "Small pets are allowed" in tool_message["content"]
    assert "not allowed" not in tool_message["content"]
