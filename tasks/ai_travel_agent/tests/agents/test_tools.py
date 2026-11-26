import json

from ai_travel_agent.agents.tools import (
    BUSINESS_TOOLS,
    POLICY_TOOLS,
    TOOL_DEFINITIONS,
    AgentToolkit,
)
from ai_travel_agent.models.enums import PendingActionType
from tests.agents.conftest import (
    CONFIRMED_BOOKING,
    RecordingProposals,
    StubApi,
    StubRetriever,
    policy_hit,
)


def _run(toolkit: AgentToolkit, name: str, **arguments: object):
    return toolkit.run(name, json.dumps(arguments))


def test_the_seven_tools_named_by_the_specification_are_offered() -> None:
    names = {tool["function"]["name"] for tool in TOOL_DEFINITIONS}
    assert names == {
        "search_packages",
        "get_package",
        "check_availability",
        "create_booking",
        "get_booking_status",
        "cancel_booking",
        "retrieve_policy",
    }
    assert names == POLICY_TOOLS | BUSINESS_TOOLS


def test_search_passes_only_validated_filters(toolkit: AgentToolkit, api: StubApi):
    outcome = _run(toolkit, "search_packages", keyword="Kashmir", max_price=25000)

    assert not outcome.is_error
    assert outcome.value[0]["price_per_traveller_inr"] == "24000.00"
    assert api.calls == [
        (
            "search_packages",
            {
                "destination": None,
                "max_price": 25000,
                "duration_days": None,
                "start_date": None,
                "keyword": "Kashmir",
            },
        )
    ]


def test_check_availability_compares_seats(toolkit: AgentToolkit) -> None:
    assert _run(toolkit, "check_availability", package_id=1, quantity=8).value[
        "can_book"
    ]
    assert not _run(toolkit, "check_availability", package_id=1, quantity=9).is_error
    assert not _run(toolkit, "check_availability", package_id=1, quantity=9).value[
        "can_book"
    ]


def test_bad_calls_come_back_as_errors_the_model_can_explain(
    toolkit: AgentToolkit,
) -> None:
    assert toolkit.run("delete_everything", "{}").is_error
    assert toolkit.run("get_package", "{not json").is_error
    assert toolkit.run("get_package", "[1]").is_error
    assert _run(toolkit, "get_package", package_id=-1).is_error
    assert _run(toolkit, "get_package", package_id=1, url="http://evil").is_error
    missing = _run(toolkit, "get_package", package_id=99)
    assert missing.is_error
    assert missing.value["code"] == "NOT_FOUND"


def test_create_booking_only_proposes_with_server_prices(
    toolkit: AgentToolkit, proposals: RecordingProposals
) -> None:
    outcome = _run(
        toolkit,
        "create_booking",
        lines=[{"package_id": 1, "title": "kashmir  explorer", "quantity": 2}],
    )

    assert not outcome.is_error
    assert outcome.value["status"] == "AWAITING_TRAVELLER_CONFIRMATION"
    assert outcome.pending_action_id == 1
    [(action_type, terms)] = proposals.proposals
    assert action_type is PendingActionType.CREATE_BOOKING
    assert terms["total"] == "48000.00"
    assert terms["lines"][0]["unit_price"] == "24000.00"
    assert terms["lines"][0]["start_date"] == "2026-12-20"


def test_create_booking_refuses_a_title_that_does_not_match_the_id(
    toolkit: AgentToolkit, proposals: RecordingProposals
) -> None:
    outcome = _run(
        toolkit,
        "create_booking",
        lines=[{"package_id": 1, "title": "Coastal Escape", "quantity": 1}],
    )

    assert outcome.is_error
    assert "Kashmir Explorer, not Coastal Escape" in outcome.value["error"]
    assert proposals.proposals == []


def test_create_booking_refuses_more_travellers_than_seats(
    toolkit: AgentToolkit, proposals: RecordingProposals
) -> None:
    outcome = _run(
        toolkit,
        "create_booking",
        lines=[{"package_id": 1, "title": "Kashmir Explorer", "quantity": 9}],
    )

    assert outcome.is_error
    assert outcome.value["seats_available"] == 8
    assert proposals.proposals == []


def test_cancel_booking_proposes_the_refund_quoted_by_the_api(
    toolkit: AgentToolkit, proposals: RecordingProposals
) -> None:
    outcome = _run(toolkit, "cancel_booking", booking_id=501)

    assert not outcome.is_error
    [(action_type, terms)] = proposals.proposals
    assert action_type is PendingActionType.CANCEL_BOOKING
    assert terms["refund_amount"] == "24000.00"
    assert terms["refund_percent"] == 100


def test_cancel_booking_refuses_a_booking_without_a_quote(
    api: StubApi, toolkit: AgentToolkit, proposals: RecordingProposals
) -> None:
    api.bookings[502] = {
        **CONFIRMED_BOOKING,
        "id": 502,
        "status": "CANCELLED",
        "cancellation_quote": None,
    }

    outcome = _run(toolkit, "cancel_booking", booking_id=502)

    assert outcome.is_error
    assert proposals.proposals == []


def test_retrieve_policy_labels_sources_and_says_when_nothing_matched(
    toolkit: AgentToolkit, retriever: StubRetriever
) -> None:
    empty = _run(toolkit, "retrieve_policy", question="Do you allow pets?")
    assert empty.value["evidence"] == []
    assert "No relevant policy text" in empty.value["note"]

    retriever.hits = [policy_hit("Full refund at least 7 days before departure.")]
    found = _run(toolkit, "retrieve_policy", question="cancellation refund")
    assert found.value["evidence"][0]["source"] == "Cancellation_Policy.pdf p.1"
    assert found.evidence == tuple(retriever.hits)
