"""Confirm or reject a booking or cancellation the agent proposed.

1. Claim: lock the action row. A decided action returns its stored outcome, so
   confirming twice gives the same answer. An expired one becomes EXPIRED. A
   rejection is final at once. A confirmation moves PENDING to PROCESSING and
   commits before any business call.
2. Execute through the REST API as the traveller, with the action's stored
   idempotency key: a retry, or two confirmations racing, replays the same
   booking instead of creating a second one. On the first attempt the stored
   terms are rechecked first (price per traveller for a booking; the refund
   for a cancellation, which drops when a policy deadline passes); changed
   terms fail the action and ask the traveller for a fresh proposal. Seats,
   departure date and ownership are rechecked by the API itself.
3. Finish: lock again, record CONFIRMED or FAILED and an assistant message
   with the outcome. If the API was unreachable, the action stays PROCESSING
   and the traveller can press Confirm again to retry safely.
"""

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from ai_travel_agent.agents.api_client import (
    ApiCallError,
    TravelApiClient,
)
from ai_travel_agent.config.database import session_scope
from ai_travel_agent.exceptions.errors import (
    AssistantUnavailableError,
    BusinessConflictError,
    ResourceNotFoundError,
)
from ai_travel_agent.models import ChatMessage, PendingAction
from ai_travel_agent.models.base import utc_now
from ai_travel_agent.models.enums import (
    BookingStatus,
    ChatMode,
    MessageRole,
    PendingActionStatus,
    PendingActionType,
)
from ai_travel_agent.models.user import User
from ai_travel_agent.repositories.chat_repository import (
    ChatRepository,
)
from ai_travel_agent.repositories.pending_action_repository import (
    PendingActionRepository,
)
from ai_travel_agent.schemas.chat import (
    ActionDecisionResponse,
    ChatMessageResponse,
    Decision,
    PendingActionResponse,
)

PENDING_ACTION = "Pending action"
DECIDED = frozenset(
    {
        PendingActionStatus.CONFIRMED,
        PendingActionStatus.REJECTED,
        PendingActionStatus.EXPIRED,
        PendingActionStatus.FAILED,
    }
)


@dataclass(frozen=True, slots=True)
class _Claim:
    """What execution needs, copied out of the claiming transaction."""

    action_type: PendingActionType
    terms: dict[str, Any]
    idempotency_key: str
    is_first_attempt: bool


@dataclass(frozen=True, slots=True)
class _Outcome:
    status: PendingActionStatus
    message: str
    result: dict[str, Any]


class PendingActionService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        api_client_factory: Callable[[int], TravelApiClient],
    ) -> None:
        self._session_factory = session_factory
        self._api_client_factory = api_client_factory

    def decide(
        self, user: User, action_id: int, decision: Decision
    ) -> ActionDecisionResponse:
        with session_scope(self._session_factory) as session:
            action = self._lock_owned(session, user, action_id)
            if action.status in DECIDED:
                return _decided_response(session, action)
            if action.status is PendingActionStatus.PROCESSING:
                if decision is Decision.REJECT:
                    raise BusinessConflictError(
                        "This proposal is already being confirmed."
                    )
                claim = _claim(action, is_first_attempt=False)
            elif action.expires_at <= utc_now():
                return _finish(
                    session,
                    action,
                    _Outcome(
                        PendingActionStatus.EXPIRED,
                        "That proposal expired before it was confirmed, so nothing "
                        "was changed. Ask me again for a fresh one.",
                        {},
                    ),
                )
            elif decision is Decision.REJECT:
                return _finish(
                    session,
                    action,
                    _Outcome(
                        PendingActionStatus.REJECTED,
                        "Okay, I've discarded that proposal. Nothing was changed.",
                        {},
                    ),
                )
            else:
                action.status = PendingActionStatus.PROCESSING
                claim = _claim(action, is_first_attempt=True)

        outcome = self._execute(user.id, claim)

        with session_scope(self._session_factory) as session:
            action = self._lock_owned(session, user, action_id)
            if action.status in DECIDED:  # a concurrent confirmation finished first
                return _decided_response(session, action)
            return _finish(session, action, outcome)

    @staticmethod
    def _lock_owned(session: Session, user: User, action_id: int) -> PendingAction:
        action = PendingActionRepository(session).lock(action_id)
        if action is None or action.user_id != user.id:
            raise ResourceNotFoundError(PENDING_ACTION, action_id)
        return action

    def _execute(self, user_id: int, claim: _Claim) -> _Outcome:
        api = self._api_client_factory(user_id)
        try:
            if claim.action_type is PendingActionType.CREATE_BOOKING:
                return _create_booking(api, claim)
            return _cancel_booking(api, claim)
        except ApiCallError as error:
            if error.status == 0 or error.status >= 500:
                # Unknown whether it happened: stay PROCESSING, allow a retry.
                raise AssistantUnavailableError(
                    "The booking service didn't respond. Press Confirm again to "
                    "retry; you won't be booked or charged twice."
                ) from error
            return _Outcome(
                PendingActionStatus.FAILED,
                f"I couldn't complete that: {error.message}",
                {"error": {"code": error.code, "message": error.message}},
            )
        finally:
            api.close()


def _create_booking(api: TravelApiClient, claim: _Claim) -> _Outcome:
    lines = claim.terms["lines"]
    if claim.is_first_attempt:
        for line in lines:
            changed = _price_change(api, line)
            if changed is not None:
                return _terms_changed(f"{changed} Nothing was booked")
    booking = api.create_booking(
        [
            {"package_id": line["package_id"], "quantity": line["quantity"]}
            for line in lines
        ],
        claim.idempotency_key,
    )
    summary = ", ".join(
        f"{line['title']} departing {line['start_date']} for {line['quantity']} "
        "traveller(s)"
        for line in lines
    )
    return _Outcome(
        PendingActionStatus.CONFIRMED,
        f"Done! Booking #{booking['id']} is confirmed: {summary}. "
        f"Total ₹{booking['total']}. Status: {booking['status']}.",
        {"booking_id": booking["id"], "total": booking["total"]},
    )


def _price_change(api: TravelApiClient, line: dict[str, Any]) -> str | None:
    try:
        package = api.get_package(line["package_id"])
    except ApiCallError as error:
        if error.status == 404:
            return f"{line['title']} is no longer offered."
        raise
    if Decimal(package["price"]) != Decimal(line["unit_price"]):
        return (
            f"The price of {line['title']} changed from ₹{line['unit_price']} "
            f"to ₹{package['price']} per traveller."
        )
    return None


def _cancel_booking(api: TravelApiClient, claim: _Claim) -> _Outcome:
    booking_id = claim.terms["booking_id"]
    current = api.get_booking(booking_id)
    if not claim.is_first_attempt and current["status"] == BookingStatus.CANCELLED:
        # A retry: the first attempt cancelled it already.
        return _cancelled(current)
    if claim.is_first_attempt:
        changed = _refund_change(current, claim.terms)
        if changed is not None:
            return _terms_changed(f"{changed} Nothing was cancelled")
    return _cancelled(api.cancel_booking(booking_id))


def _refund_change(booking: dict[str, Any], terms: dict[str, Any]) -> str | None:
    quote = booking.get("cancellation_quote")
    if quote is None:
        return f"Booking #{booking['id']} can no longer be cancelled."
    if Decimal(quote["refund_amount"]) != Decimal(terms["refund_amount"]):
        return (
            f"The refund for booking #{booking['id']} changed from "
            f"₹{terms['refund_amount']} to ₹{quote['refund_amount']}, because a "
            "cancellation deadline has passed."
        )
    return None


def _terms_changed(message: str) -> _Outcome:
    return _Outcome(
        PendingActionStatus.FAILED,
        f"{message}; ask me for a new proposal.",
        {"error": {"code": "TERMS_CHANGED", "message": message}},
    )


def _cancelled(booking: dict[str, Any]) -> _Outcome:
    return _Outcome(
        PendingActionStatus.CONFIRMED,
        f"Booking #{booking['id']} is cancelled and its seats are released. "
        f"₹{booking['refund_amount']} of ₹{booking['total']} will be refunded "
        "within 7 business days.",
        {
            "booking_id": booking["id"],
            "status": booking["status"],
            "refund_amount": booking["refund_amount"],
        },
    )


def _claim(action: PendingAction, *, is_first_attempt: bool) -> _Claim:
    return _Claim(
        action_type=action.action,
        terms=dict(action.payload),
        idempotency_key=action.idempotency_key,
        is_first_attempt=is_first_attempt,
    )


def _finish(
    session: Session, action: PendingAction, outcome: _Outcome
) -> ActionDecisionResponse:
    repository = ChatRepository(session)
    chat = repository.lock_session(action.session_id)
    if chat is None:
        raise ResourceNotFoundError("Chat session", action.session_id)
    # The proposal message keeps the action card; this one only reports.
    metadata: dict[str, Any] = {"mode": ChatMode.AGENT, "outcome_of": action.id}
    if outcome.status is PendingActionStatus.FAILED:
        metadata["error"] = outcome.result.get("error")
    message = repository.append_message(
        chat, MessageRole.ASSISTANT, outcome.message, metadata
    )
    action.status = outcome.status
    action.result = {**outcome.result, "message_id": message.id}
    session.flush()
    actions = {action.id: action}
    return ActionDecisionResponse(
        action=PendingActionResponse.from_action(action),
        message=ChatMessageResponse.from_message(message, actions),
    )


def _decided_response(
    session: Session, action: PendingAction
) -> ActionDecisionResponse:
    message_id = (action.result or {}).get("message_id")
    message = session.get(ChatMessage, message_id) if message_id else None
    if message is None:
        raise ResourceNotFoundError("Outcome message of pending action", action.id)
    return ActionDecisionResponse(
        action=PendingActionResponse.from_action(action),
        message=ChatMessageResponse.from_message(message, {action.id: action}),
    )
