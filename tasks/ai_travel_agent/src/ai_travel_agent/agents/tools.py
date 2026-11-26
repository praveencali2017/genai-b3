"""The agent's seven tools: their schemas and the code that runs them.

`AgentToolkit.run(name, raw_arguments)` always returns a `ToolOutcome`; a bad
call (unknown tool, malformed arguments, an API refusal such as "not enough
seats") comes back as an error result the model can explain. Failures of an AI
dependency (`AiServiceError`, e.g. the embedding API is down) propagate and fail
the turn instead: fed back, the model would narrate an outage as "the policy
doesn't say".

`create_booking` and `cancel_booking` never write a booking. They read the live
terms through the API (price per traveller, seats, the refund quote the booking
service computes), store them as a pending action via `propose`, and the
traveller executes them with the Confirm button (see PendingActionService).
"""

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
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

from ai_travel_agent.agents.api_client import (
    ApiCallError,
    TravelApiClient,
)
from ai_travel_agent.models.enums import PendingActionType
from ai_travel_agent.rag.retriever import PolicyRetriever
from ai_travel_agent.rag.vector_store import ChunkHit

logger = logging.getLogger(__name__)

SEARCH_PACKAGES = "search_packages"
GET_PACKAGE = "get_package"
CHECK_AVAILABILITY = "check_availability"
GET_BOOKING_STATUS = "get_booking_status"
RETRIEVE_POLICY = "retrieve_policy"
CREATE_BOOKING = "create_booking"
CANCEL_BOOKING = "cancel_booking"

POLICY_TOOLS = frozenset({RETRIEVE_POLICY})
BUSINESS_TOOLS = frozenset(
    {
        SEARCH_PACKAGES,
        GET_PACKAGE,
        CHECK_AVAILABILITY,
        GET_BOOKING_STATUS,
        CREATE_BOOKING,
        CANCEL_BOOKING,
    }
)
# The tools that only propose a change; nothing else may lead to a write.
PROPOSAL_TOOLS = frozenset({CREATE_BOOKING, CANCEL_BOOKING})


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


Travellers = Annotated[int, Field(ge=1, le=10)]


class SearchArguments(_Arguments):
    destination: Annotated[str, StringConstraints(max_length=80)] | None = None
    max_price: Annotated[Decimal, Field(gt=0)] | None = None
    duration_days: Annotated[int, Field(ge=1, le=60)] | None = None
    start_date: date | None = None
    keyword: Annotated[str, StringConstraints(max_length=100)] | None = None


class PackageArguments(_Arguments):
    package_id: PositiveInt


class AvailabilityArguments(_Arguments):
    package_id: PositiveInt
    quantity: Travellers


class BookingArguments(_Arguments):
    booking_id: PositiveInt


class PolicyArguments(_Arguments):
    question: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=3, max_length=500)
    ]


class BookingLineArguments(_Arguments):
    package_id: PositiveInt
    # The package the model believes package_id is; checked against the
    # catalog so a guessed or mixed-up id can't silently book the wrong trip.
    title: Annotated[str, StringConstraints(strip_whitespace=True, max_length=120)]
    quantity: Travellers


def _distinct(lines: list[BookingLineArguments]) -> list[BookingLineArguments]:
    if len({line.package_id for line in lines}) != len(lines):
        raise ValueError("list each package once with its total travellers")
    return lines


class CreateBookingArguments(_Arguments):
    lines: Annotated[
        list[BookingLineArguments],
        Field(min_length=1, max_length=5),
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


_PACKAGE_ID = {"type": "integer", "description": "Package id from a search result."}
_BOOKING_ID = {"type": "integer", "description": "The traveller's booking id."}
_TRAVELLERS = {
    "type": "integer",
    "minimum": 1,
    "maximum": 10,
    "description": "Number of travellers (seats).",
}

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    _tool(
        SEARCH_PACKAGES,
        "Search the live catalog of upcoming tour packages. Returns id, title, "
        "destination, duration, departure and return dates, price per "
        "traveller (INR) and seats available. All filters optional.",
        {
            "destination": {
                "type": "string",
                "description": "City or region, e.g. Kashmir, Srinagar, Goa.",
            },
            "max_price": {
                "type": "number",
                "description": "Highest price per traveller in INR.",
            },
            "duration_days": {"type": "integer", "description": "Exact trip length."},
            "start_date": {
                "type": "string",
                "description": "Departing on or after this date (YYYY-MM-DD).",
            },
            "keyword": {
                "type": "string",
                "description": "Word in the title or description, e.g. Kashmir.",
            },
        },
    ),
    _tool(
        GET_PACKAGE,
        "Current details, price and seats available of one package.",
        {"package_id": _PACKAGE_ID},
        ["package_id"],
    ),
    _tool(
        CHECK_AVAILABILITY,
        "Check whether a number of travellers can be booked on a package now.",
        {"package_id": _PACKAGE_ID, "quantity": _TRAVELLERS},
        ["package_id", "quantity"],
    ),
    _tool(
        GET_BOOKING_STATUS,
        "Status, lines, total and current refund-if-cancelled of one of the "
        "traveller's bookings.",
        {"booking_id": _BOOKING_ID},
        ["booking_id"],
    ),
    _tool(
        RETRIEVE_POLICY,
        "Search the agency's policy documents (cancellation and refunds, "
        "travel rules, ID and baggage, destination guide, FAQ). Returns "
        "excerpts with sources; an empty list means the documents don't cover "
        "the question.",
        {"question": {"type": "string", "description": "A standalone question."}},
        ["question"],
    ),
    _tool(
        CREATE_BOOKING,
        "Propose a booking for the traveller to confirm. Does NOT book it: the "
        "traveller must press Confirm. Returns the exact terms to show.",
        {
            "lines": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "properties": {
                        "package_id": _PACKAGE_ID,
                        "title": {
                            "type": "string",
                            "description": "The package title exactly as the "
                            "catalog tool returned it for this package_id.",
                        },
                        "quantity": _TRAVELLERS,
                    },
                    "required": ["package_id", "title", "quantity"],
                    "additionalProperties": False,
                },
            }
        },
        ["lines"],
    ),
    _tool(
        CANCEL_BOOKING,
        "Propose cancelling one of the traveller's bookings. Does NOT cancel "
        "it: the traveller must press Confirm. Returns the refund it would "
        "give under the cancellation policy.",
        {"booking_id": _BOOKING_ID},
        ["booking_id"],
    ),
]


# ------------------------------------------------------------------ toolkit


@dataclass
class AgentToolkit:
    """Runs tools for one chat turn of one user."""

    api: TravelApiClient
    retriever: PolicyRetriever
    propose: ProposeAction
    _handlers: dict[str, Callable[[dict[str, Any]], ToolOutcome]] = field(
        init=False, repr=False
    )

    def __post_init__(self) -> None:
        self._handlers = {
            SEARCH_PACKAGES: self._search_packages,
            GET_PACKAGE: self._get_package,
            CHECK_AVAILABILITY: self._check_availability,
            GET_BOOKING_STATUS: self._get_booking_status,
            RETRIEVE_POLICY: self._retrieve_policy,
            CREATE_BOOKING: self._create_booking,
            CANCEL_BOOKING: self._cancel_booking,
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

    def _search_packages(self, arguments: dict[str, Any]) -> ToolOutcome:
        parsed = SearchArguments.model_validate(arguments)
        found = self.api.search_packages(parsed.model_dump())
        packages = [_package_summary(package) for package in found]
        return ToolOutcome(
            packages, is_error=False, summary=f"{len(packages)} package(s)"
        )

    def _get_package(self, arguments: dict[str, Any]) -> ToolOutcome:
        parsed = PackageArguments.model_validate(arguments)
        package = self.api.get_package(parsed.package_id)
        return ToolOutcome(
            _package_summary(package), is_error=False, summary=package["title"]
        )

    def _check_availability(self, arguments: dict[str, Any]) -> ToolOutcome:
        parsed = AvailabilityArguments.model_validate(arguments)
        package = self.api.get_package(parsed.package_id)
        available = int(package["capacity"])
        value = {
            "package_id": package["id"],
            "title": package["title"],
            "start_date": package["start_date"],
            "requested": parsed.quantity,
            "seats_available": available,
            "can_book": available >= parsed.quantity,
            "price_per_traveller_inr": package["price"],
        }
        verdict = "available" if value["can_book"] else "not enough seats"
        return ToolOutcome(
            value, is_error=False, summary=f"{package['title']}: {verdict}"
        )

    def _get_booking_status(self, arguments: dict[str, Any]) -> ToolOutcome:
        parsed = BookingArguments.model_validate(arguments)
        booking = self.api.get_booking(parsed.booking_id)
        return ToolOutcome(
            _booking_summary(booking),
            is_error=False,
            summary=f"booking #{booking['id']} {booking['status']}",
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
                "source": hit.source_label,
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

    def _create_booking(self, arguments: dict[str, Any]) -> ToolOutcome:
        parsed = CreateBookingArguments.model_validate(arguments)
        lines: list[dict[str, Any]] = []
        for requested in parsed.lines:
            package = self.api.get_package(requested.package_id)
            if _normalized(package["title"]) != _normalized(requested.title):
                return ToolOutcome.error(
                    f"Package {package['id']} is {package['title']}, not "
                    f"{requested.title}. Look up the right package_id with "
                    "search_packages first.",
                    package_id=package["id"],
                )
            available = int(package["capacity"])
            if requested.quantity > available:
                return ToolOutcome.error(
                    f"Only {available} seat(s) are left on {package['title']}.",
                    package_id=package["id"],
                    seats_available=available,
                )
            unit_price = Decimal(package["price"])
            lines.append(
                {
                    "package_id": package["id"],
                    "title": package["title"],
                    "destination": package["destination"],
                    "start_date": package["start_date"],
                    "end_date": package["end_date"],
                    "quantity": requested.quantity,
                    "unit_price": str(unit_price),
                    "line_total": str(unit_price * requested.quantity),
                }
            )
        total = sum((Decimal(line["line_total"]) for line in lines), Decimal("0"))
        terms = {"lines": lines, "total": str(total)}
        proposal = self.propose(PendingActionType.CREATE_BOOKING, terms)
        return self._awaiting_confirmation(
            proposal, terms, f"booking proposal ₹{total}"
        )

    def _cancel_booking(self, arguments: dict[str, Any]) -> ToolOutcome:
        parsed = BookingArguments.model_validate(arguments)
        booking = self.api.get_booking(parsed.booking_id)
        quote = booking.get("cancellation_quote")
        if quote is None:
            return ToolOutcome.error(
                f"Booking #{booking['id']} is {booking['status']} and can't be "
                "cancelled; only confirmed bookings that haven't departed can be.",
                booking_status=booking["status"],
            )
        terms = {
            "booking_id": booking["id"],
            "total": booking["total"],
            "refund_amount": quote["refund_amount"],
            "refund_percent": quote["refund_percent"],
            "days_before_departure": quote["days_before_departure"],
            "lines": [
                {
                    "title": line["title"],
                    "start_date": line["start_date"],
                    "quantity": line["quantity"],
                }
                for line in booking["lines"]
            ],
            "impact": f"Booking #{booking['id']} will be cancelled, its seats "
            f"released, and ₹{quote['refund_amount']} of ₹{booking['total']} "
            "refunded under the cancellation policy.",
        }
        proposal = self.propose(PendingActionType.CANCEL_BOOKING, terms)
        return self._awaiting_confirmation(
            proposal, terms, f"cancellation proposal #{booking['id']}"
        )

    @staticmethod
    def _awaiting_confirmation(
        proposal: ProposedAction, terms: dict[str, Any], summary: str
    ) -> ToolOutcome:
        value = {
            "status": "AWAITING_TRAVELLER_CONFIRMATION",
            "pending_action_id": proposal.id,
            "expires_at": proposal.expires_at.isoformat(timespec="minutes") + "Z",
            "terms": terms,
            "next_step": "Show these exact terms and ask the traveller to press "
            "Confirm or Reject. Nothing has been executed.",
        }
        return ToolOutcome(
            value, is_error=False, summary=summary, pending_action_id=proposal.id
        )


def _package_summary(package: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": package["id"],
        "title": package["title"],
        "destination": package["destination"],
        "duration_days": package["duration_days"],
        "start_date": package["start_date"],
        "end_date": package["end_date"],
        "price_per_traveller_inr": package["price"],
        "seats_available": package["capacity"],
        "description": package.get("description"),
    }


def _booking_summary(booking: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": booking["id"],
        "status": booking["status"],
        "total_inr": booking["total"],
        "refund_amount_inr": booking.get("refund_amount"),
        "booked_at": booking["created_at"],
        "refund_if_cancelled_today": booking.get("cancellation_quote"),
        "lines": [
            {
                "title": line["title"],
                "destination": line["destination"],
                "start_date": line["start_date"],
                "quantity": line["quantity"],
                "unit_price": line["unit_price"],
            }
            for line in booking["lines"]
        ],
    }


def _normalized(title: str) -> str:
    return " ".join(title.casefold().split())
