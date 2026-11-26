"""The agent's seven tools: their schemas and the code that runs them.

`AgentToolkit.run(name, raw_arguments)` always returns a `ToolOutcome`; a bad
call (unknown tool, malformed arguments, an API refusal such as "not enough
portions") comes back as an error result the model can explain. Failures of an
AI dependency (`AiServiceError`, e.g. the embedding API is down) propagate and
fail the turn instead: fed back, the model would narrate an outage as "the
policy doesn't say".

`create_order` and `cancel_order` never write an order. They read the live
terms through the API, store them as a pending action via `propose`, and the
customer executes them with the Confirm button (see PendingActionService).
"""

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    PositiveInt,
    StringConstraints,
    ValidationError,
)

from ai_restaurant_ordering_assistant.agents.api_client import (
    ApiCallError,
    RestaurantApiClient,
)
from ai_restaurant_ordering_assistant.models.enums import (
    OrderStatus,
    PendingActionType,
)
from ai_restaurant_ordering_assistant.rag.retriever import PolicyRetriever
from ai_restaurant_ordering_assistant.rag.vector_store import ChunkHit

logger = logging.getLogger(__name__)

SEARCH_MENU_ITEMS = "search_menu_items"
GET_MENU_ITEM = "get_menu_item"
CHECK_AVAILABILITY = "check_availability"
GET_ORDER_STATUS = "get_order_status"
RETRIEVE_POLICY = "retrieve_policy"
CREATE_ORDER = "create_order"
CANCEL_ORDER = "cancel_order"

POLICY_TOOLS = frozenset({RETRIEVE_POLICY})
BUSINESS_TOOLS = frozenset(
    {
        SEARCH_MENU_ITEMS,
        GET_MENU_ITEM,
        CHECK_AVAILABILITY,
        GET_ORDER_STATUS,
        CREATE_ORDER,
        CANCEL_ORDER,
    }
)


@dataclass(frozen=True, slots=True)
class ProposedAction:
    id: int
    expires_at: datetime


# Stores a pending action for the current chat session and user.
ProposeAction = Callable[[PendingActionType, dict[str, Any]], ProposedAction]


@dataclass(frozen=True, slots=True)
class ToolOutcome:
    """`value` is what the model sees (JSON-serializable)."""

    value: Any
    is_error: bool
    summary: str
    evidence: tuple[ChunkHit, ...] = ()
    pending_action_id: int | None = None

    def to_json(self) -> str:
        return json.dumps(self.value, ensure_ascii=False, default=str)

    @classmethod
    def error(cls, message: str, **details: Any) -> Self:
        return cls({"error": message, **details}, is_error=True, summary=message)


# ------------------------------------------------------------------ arguments


class _Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


Quantity = Annotated[int, Field(ge=1, le=10)]


class SearchArguments(_Arguments):
    keyword: Annotated[str, StringConstraints(max_length=100)] | None = None
    category: Annotated[str, StringConstraints(max_length=50)] | None = None
    vegetarian: bool | None = None
    max_price: Annotated[Decimal, Field(gt=0)] | None = None


class ItemArguments(_Arguments):
    item_id: PositiveInt


class AvailabilityArguments(_Arguments):
    item_id: PositiveInt
    quantity: Quantity


class OrderArguments(_Arguments):
    order_id: PositiveInt


class PolicyArguments(_Arguments):
    question: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=3, max_length=500)
    ]


class OrderLineArguments(_Arguments):
    item_id: PositiveInt
    # The dish the model believes item_id is; checked against the menu so a
    # guessed or mixed-up id can't silently order the wrong dish.
    title: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)]
    quantity: Quantity


def _distinct(lines: list[OrderLineArguments]) -> list[OrderLineArguments]:
    if len({line.item_id for line in lines}) != len(lines):
        raise ValueError("list each item once with its total quantity")
    return lines


class CreateOrderArguments(_Arguments):
    lines: Annotated[
        list[OrderLineArguments],
        Field(min_length=1, max_length=20),
        AfterValidator(_distinct),
    ]


# ------------------------------------------------------------------ schemas


def _tool(name: str, description: str, properties: dict[str, Any], required=()):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": list(required),
                "additionalProperties": False,
            },
        },
    }


_ITEM_ID = {"type": "integer", "description": "Menu item id from a search result."}
_ORDER_ID = {"type": "integer", "description": "The customer's order id."}
_QUANTITY = {"type": "integer", "minimum": 1, "maximum": 10}

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    _tool(
        SEARCH_MENU_ITEMS,
        "Search the live menu. Returns active dishes with id, price (INR), "
        "vegetarian flag, category and portions available. All filters optional.",
        {
            "keyword": {"type": "string", "description": "Word in name/description."},
            "category": {
                "type": "string",
                "description": "Breakfast, Main course, Dessert or Beverages.",
            },
            "vegetarian": {"type": "boolean"},
            "max_price": {"type": "number", "description": "Highest price in INR."},
        },
    ),
    _tool(
        GET_MENU_ITEM,
        "Current details, price and available portions of one dish.",
        {"item_id": _ITEM_ID},
        ["item_id"],
    ),
    _tool(
        CHECK_AVAILABILITY,
        "Check whether a quantity of a dish can be ordered right now.",
        {"item_id": _ITEM_ID, "quantity": _QUANTITY},
        ["item_id", "quantity"],
    ),
    _tool(
        GET_ORDER_STATUS,
        "Status, lines and total of one of the customer's orders.",
        {"order_id": _ORDER_ID},
        ["order_id"],
    ),
    _tool(
        RETRIEVE_POLICY,
        "Search the restaurant's policy documents (cancellation, refunds, "
        "delivery, ingredients and allergens). Returns excerpts with sources; "
        "an empty list means the documents don't cover the question.",
        {"question": {"type": "string", "description": "A standalone question."}},
        ["question"],
    ),
    _tool(
        CREATE_ORDER,
        "Propose an order for the customer to confirm. Does NOT place it: the "
        "customer must press Confirm. Returns the exact terms to show.",
        {
            "lines": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "properties": {
                        "item_id": _ITEM_ID,
                        "title": {
                            "type": "string",
                            "description": "The dish name exactly as the menu "
                            "tool returned it for this item_id.",
                        },
                        "quantity": _QUANTITY,
                    },
                    "required": ["item_id", "title", "quantity"],
                    "additionalProperties": False,
                },
            }
        },
        ["lines"],
    ),
    _tool(
        CANCEL_ORDER,
        "Propose cancelling one of the customer's orders. Does NOT cancel it: "
        "the customer must press Confirm. Only CONFIRMED orders can be cancelled.",
        {"order_id": _ORDER_ID},
        ["order_id"],
    ),
]


# ------------------------------------------------------------------ toolkit


@dataclass
class AgentToolkit:
    """Runs tools for one chat turn of one user."""

    api: RestaurantApiClient
    retriever: PolicyRetriever
    propose: ProposeAction
    _handlers: dict[str, Callable[[dict[str, Any]], ToolOutcome]] = field(
        init=False, repr=False
    )

    def __post_init__(self) -> None:
        self._handlers = {
            SEARCH_MENU_ITEMS: self._search_menu_items,
            GET_MENU_ITEM: self._get_menu_item,
            CHECK_AVAILABILITY: self._check_availability,
            GET_ORDER_STATUS: self._get_order_status,
            RETRIEVE_POLICY: self._retrieve_policy,
            CREATE_ORDER: self._create_order,
            CANCEL_ORDER: self._cancel_order,
        }

    def run(self, name: str, raw_arguments: str) -> ToolOutcome:
        outcome = self._run(name, raw_arguments)
        logger.info("Tool %s -> %s", name, outcome.summary)
        return outcome

    def _run(self, name: str, raw_arguments: str) -> ToolOutcome:
        handler = self._handlers.get(name)
        if handler is None:
            return ToolOutcome.error(f"Unknown tool {name!r}.")
        try:
            arguments = json.loads(raw_arguments or "{}")
        except json.JSONDecodeError as error:
            return ToolOutcome.error(f"Arguments are not valid JSON: {error}")
        if not isinstance(arguments, dict):
            return ToolOutcome.error("Arguments must be a JSON object.")
        # Some providers send null for omitted optional arguments.
        arguments = {k: v for k, v in arguments.items() if v is not None}
        try:
            return handler(arguments)
        except ValidationError as error:
            problems = error.errors(include_url=False, include_context=False)
            return ToolOutcome.error("Invalid arguments.", details=problems)
        except ApiCallError as error:
            return ToolOutcome.error(error.message, code=error.code)

    # -------------------------------------------------------------- reads

    def _search_menu_items(self, arguments: dict[str, Any]) -> ToolOutcome:
        parsed = SearchArguments.model_validate(arguments)
        items = self.api.search_menu(parsed.model_dump())
        dishes = [_dish(item) for item in items]
        return ToolOutcome(dishes, is_error=False, summary=f"{len(dishes)} dish(es)")

    def _get_menu_item(self, arguments: dict[str, Any]) -> ToolOutcome:
        parsed = ItemArguments.model_validate(arguments)
        item = self.api.get_menu_item(parsed.item_id)
        return ToolOutcome(_dish(item), is_error=False, summary=item["title"])

    def _check_availability(self, arguments: dict[str, Any]) -> ToolOutcome:
        parsed = AvailabilityArguments.model_validate(arguments)
        item = self.api.get_menu_item(parsed.item_id)
        available = int(item["capacity"])
        value = {
            "item_id": item["id"],
            "title": item["title"],
            "requested": parsed.quantity,
            "portions_available": available,
            "can_order": available >= parsed.quantity,
            "unit_price": item["price"],
        }
        verdict = "available" if value["can_order"] else "not enough portions"
        return ToolOutcome(value, is_error=False, summary=f"{item['title']}: {verdict}")

    def _get_order_status(self, arguments: dict[str, Any]) -> ToolOutcome:
        parsed = OrderArguments.model_validate(arguments)
        order = self.api.get_order(parsed.order_id)
        return ToolOutcome(
            _order_summary(order),
            is_error=False,
            summary=f"order #{order['id']} {order['status']}",
        )

    def _retrieve_policy(self, arguments: dict[str, Any]) -> ToolOutcome:
        parsed = PolicyArguments.model_validate(arguments)
        hits = self.retriever.retrieve(parsed.question)
        if not hits:
            return ToolOutcome(
                {"evidence": [], "note": "No relevant policy text was found."},
                is_error=False,
                summary="no evidence",
            )
        evidence = [
            {
                "source": _source_label(hit),
                "chunk_id": hit.chunk_id,
                "relevance": round(hit.score, 3),
                "excerpt": hit.text,
            }
            for hit in hits
        ]
        return ToolOutcome(
            {"evidence": evidence},
            is_error=False,
            summary=f"{len(hits)} excerpt(s)",
            evidence=tuple(hits),
        )

    # ------------------------------------------------------- proposals

    def _create_order(self, arguments: dict[str, Any]) -> ToolOutcome:
        parsed = CreateOrderArguments.model_validate(arguments)
        lines: list[dict[str, Any]] = []
        for requested in parsed.lines:
            item = self.api.get_menu_item(requested.item_id)
            if _normalized(item["title"]) != _normalized(requested.title):
                return ToolOutcome.error(
                    f"Item {item['id']} is {item['title']}, not {requested.title}. "
                    "Look up the right item_id with search_menu_items first.",
                    item_id=item["id"],
                )
            available = int(item["capacity"])
            if requested.quantity > available:
                return ToolOutcome.error(
                    f"Only {available} portion(s) of {item['title']} are available.",
                    item_id=item["id"],
                    portions_available=available,
                )
            unit_price = Decimal(item["price"])
            lines.append(
                {
                    "item_id": item["id"],
                    "title": item["title"],
                    "quantity": requested.quantity,
                    "unit_price": str(unit_price),
                    "line_total": str(unit_price * requested.quantity),
                }
            )
        total = sum((Decimal(line["line_total"]) for line in lines), Decimal("0"))
        terms = {"lines": lines, "total": str(total)}
        proposal = self.propose(PendingActionType.CREATE_ORDER, terms)
        return self._awaiting_confirmation(proposal, terms, f"order proposal ₹{total}")

    def _cancel_order(self, arguments: dict[str, Any]) -> ToolOutcome:
        parsed = OrderArguments.model_validate(arguments)
        order = self.api.get_order(parsed.order_id)
        if order["status"] != OrderStatus.CONFIRMED:
            return ToolOutcome.error(
                f"Order #{order['id']} is {order['status']} and can no longer be "
                "cancelled; only orders not yet in preparation can be.",
                order_status=order["status"],
            )
        terms = {
            "order_id": order["id"],
            "total": order["total"],
            "lines": [
                {"title": line["title"], "quantity": line["quantity"]}
                for line in order["lines"]
            ],
            "impact": f"Order #{order['id']} will be cancelled, its portions "
            f"returned to stock, and ₹{order['total']} refunded per the refund "
            "policy.",
        }
        proposal = self.propose(PendingActionType.CANCEL_ORDER, terms)
        return self._awaiting_confirmation(
            proposal, terms, f"cancellation proposal #{order['id']}"
        )

    @staticmethod
    def _awaiting_confirmation(
        proposal: ProposedAction, terms: dict[str, Any], summary: str
    ) -> ToolOutcome:
        value = {
            "status": "AWAITING_CUSTOMER_CONFIRMATION",
            "pending_action_id": proposal.id,
            "expires_at": proposal.expires_at.isoformat(timespec="minutes") + "Z",
            "terms": terms,
            "next_step": "Show these exact terms and ask the customer to press "
            "Confirm or Reject. Nothing has been executed.",
        }
        return ToolOutcome(
            value, is_error=False, summary=summary, pending_action_id=proposal.id
        )


def _dish(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item["id"],
        "title": item["title"],
        "category": item["category"],
        "vegetarian": item["vegetarian"],
        "price_inr": item["price"],
        "portions_available": item["capacity"],
        "description": item.get("description"),
    }


def _order_summary(order: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": order["id"],
        "status": order["status"],
        "total_inr": order["total"],
        "placed_at": order["created_at"],
        "lines": [
            {
                "title": line["title"],
                "quantity": line["quantity"],
                "unit_price": line["unit_price"],
            }
            for line in order["lines"]
        ],
    }


def _normalized(title: str) -> str:
    return " ".join(title.casefold().split())


def _source_label(hit: ChunkHit) -> str:
    page = f" p.{hit.page}" if hit.page is not None else ""
    return f"{hit.filename}{page}"
