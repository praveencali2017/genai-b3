"""Confirm or reject an order or cancellation the agent proposed.

1. Claim: lock the action row. A decided action returns its stored outcome, so
   confirming twice gives the same answer. An expired one becomes EXPIRED. A
   rejection is final at once. A confirmation moves PENDING to PROCESSING and
   commits before any business call.
2. Execute through the REST API as the customer, with the action's stored
   idempotency key: a retry, or two confirmations racing, replays the same
   order instead of creating a second one. On the first attempt the stored
   prices are rechecked first; a changed price fails the action and asks the
   customer to request a fresh proposal.
3. Finish: lock again, record CONFIRMED or FAILED and an assistant message
   with the outcome. If the API was unreachable, the action stays PROCESSING
   and the customer can press Confirm again to retry safely.
"""

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from ai_restaurant_ordering_assistant.agents.api_client import (
    ApiCallError,
    RestaurantApiClient,
)
from ai_restaurant_ordering_assistant.config.database import session_scope
from ai_restaurant_ordering_assistant.exceptions.errors import (
    AssistantUnavailableError,
    BusinessConflictError,
    ResourceNotFoundError,
)
from ai_restaurant_ordering_assistant.models import ChatMessage, PendingAction
from ai_restaurant_ordering_assistant.models.base import utc_now
from ai_restaurant_ordering_assistant.models.enums import (
    ChatMode,
    MessageRole,
    OrderStatus,
    PendingActionStatus,
    PendingActionType,
)
from ai_restaurant_ordering_assistant.models.user import User
from ai_restaurant_ordering_assistant.repositories.chat_repository import (
    ChatRepository,
)
from ai_restaurant_ordering_assistant.repositories.pending_action_repository import (
    PendingActionRepository,
)
from ai_restaurant_ordering_assistant.schemas.chat import (
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
        api_client_factory: Callable[[int], RestaurantApiClient],
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
            if claim.action_type is PendingActionType.CREATE_ORDER:
                return _place_order(api, claim)
            return _cancel_order(api, claim)
        except ApiCallError as error:
            if error.status == 0 or error.status >= 500:
                # Unknown whether it happened: stay PROCESSING, allow a retry.
                raise AssistantUnavailableError(
                    "The ordering service didn't respond. Press Confirm again to "
                    "retry; you won't be charged twice."
                ) from error
            return _Outcome(
                PendingActionStatus.FAILED,
                f"I couldn't complete that: {error.message}",
                {"error": {"code": error.code, "message": error.message}},
            )
        finally:
            api.close()


def _place_order(api: RestaurantApiClient, claim: _Claim) -> _Outcome:
    lines = claim.terms["lines"]
    if claim.is_first_attempt:
        for line in lines:
            changed = _price_change(api, line)
            if changed is not None:
                return _Outcome(
                    PendingActionStatus.FAILED,
                    f"{changed} Nothing was ordered; ask me for a new proposal.",
                    {"error": {"code": "TERMS_CHANGED", "message": changed}},
                )
    order = api.place_order(
        [{"item_id": line["item_id"], "quantity": line["quantity"]} for line in lines],
        claim.idempotency_key,
    )
    summary = ", ".join(f"{line['quantity']} × {line['title']}" for line in lines)
    return _Outcome(
        PendingActionStatus.CONFIRMED,
        f"Done! Order #{order['id']} is placed: {summary}. Total ₹{order['total']}. "
        f"Status: {order['status']}.",
        {"order_id": order["id"], "total": order["total"]},
    )


def _price_change(api: RestaurantApiClient, line: dict[str, Any]) -> str | None:
    try:
        item = api.get_menu_item(line["item_id"])
    except ApiCallError as error:
        if error.status == 404:
            return f"{line['title']} is no longer on the menu."
        raise
    if Decimal(item["price"]) != Decimal(line["unit_price"]):
        return (
            f"The price of {line['title']} changed from ₹{line['unit_price']} "
            f"to ₹{item['price']}."
        )
    return None


def _cancel_order(api: RestaurantApiClient, claim: _Claim) -> _Outcome:
    order_id = claim.terms["order_id"]
    if not claim.is_first_attempt:
        # A retry: the first attempt may have cancelled it already.
        current = api.get_order(order_id)
        if current["status"] == OrderStatus.CANCELLED:
            return _cancelled(current)
    return _cancelled(api.cancel_order(order_id))


def _cancelled(order: dict[str, Any]) -> _Outcome:
    return _Outcome(
        PendingActionStatus.CONFIRMED,
        f"Order #{order['id']} is cancelled and its portions are back in stock. "
        f"₹{order['total']} will be refunded within 3–5 business days.",
        {"order_id": order["id"], "status": order["status"]},
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
