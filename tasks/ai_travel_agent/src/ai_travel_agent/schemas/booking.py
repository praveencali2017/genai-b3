"""Booking request and response bodies."""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Self

from pydantic import (
    AfterValidator,
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    PositiveInt,
    StringConstraints,
)

from ai_travel_agent.models import Booking, BookingLine
from ai_travel_agent.models.enums import BookingStatus
from ai_travel_agent.services.cancellation_policy import CancellationQuote

MAX_LINES_PER_BOOKING = 5
MAX_TRAVELLERS_PER_LINE = 10

IdempotencyKey = Annotated[
    str,
    StringConstraints(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._:-]+$"),
]


class BookingLineCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    # `item_id` is the name the capstone's API contract uses for the same thing.
    package_id: PositiveInt = Field(
        validation_alias=AliasChoices("package_id", "item_id")
    )
    quantity: Annotated[int, Field(ge=1, le=MAX_TRAVELLERS_PER_LINE)] = Field(
        description="Number of travellers."
    )


def _distinct_packages(lines: list[BookingLineCreate]) -> list[BookingLineCreate]:
    package_ids = [line.package_id for line in lines]
    if len(package_ids) != len(set(package_ids)):
        raise ValueError("each package may appear only once; combine the travellers")
    return lines


class BookingCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lines: Annotated[
        list[BookingLineCreate],
        Field(min_length=1, max_length=MAX_LINES_PER_BOOKING),
        AfterValidator(_distinct_packages),
    ]

    def quantities(self) -> dict[int, int]:
        """Travellers per package id, the booking's identity for idempotent
        replays."""
        return {line.package_id: line.quantity for line in self.lines}


class BookingStatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: BookingStatus


class BookingLineResponse(BaseModel):
    package_id: int
    title: str
    destination: str
    start_date: date
    quantity: int
    unit_price: Decimal
    line_total: Decimal

    @classmethod
    def from_line(cls, line: BookingLine) -> Self:
        return cls(
            package_id=line.package_id,
            title=line.package.title,
            destination=line.package.destination,
            start_date=line.package.start_date,
            quantity=line.quantity,
            unit_price=line.unit_price,
            line_total=line.line_total,
        )


class CancellationQuoteResponse(BaseModel):
    """What cancelling today would refund, per the cancellation policy."""

    refund_amount: Decimal
    refund_percent: int | None = Field(
        description="The percent refunded, or null if lines differ."
    )
    days_before_departure: int = Field(description="Days until the first departure.")

    @classmethod
    def from_quote(cls, quote: CancellationQuote) -> Self:
        percents = {line.refund_percent for line in quote.lines}
        return cls(
            refund_amount=quote.refund_amount,
            refund_percent=percents.pop() if len(percents) == 1 else None,
            days_before_departure=min(
                line.days_before_departure for line in quote.lines
            ),
        )


class BookingResponse(BaseModel):
    id: int
    user_id: int
    status: BookingStatus
    total: Decimal
    refund_amount: Decimal | None
    cancelled_at: datetime | None
    created_at: datetime
    updated_at: datetime
    lines: list[BookingLineResponse]
    cancellation_quote: CancellationQuoteResponse | None = Field(
        description="Refund if the owner cancelled today; null when the booking "
        "can't be cancelled."
    )

    @classmethod
    def from_booking(cls, booking: Booking, quote: CancellationQuote | None) -> Self:
        return cls(
            id=booking.id,
            user_id=booking.user_id,
            status=booking.status,
            total=booking.total,
            refund_amount=booking.refund_amount,
            cancelled_at=booking.cancelled_at,
            created_at=booking.created_at,
            updated_at=booking.updated_at,
            lines=[BookingLineResponse.from_line(line) for line in booking.lines],
            cancellation_quote=(
                CancellationQuoteResponse.from_quote(quote) if quote else None
            ),
        )
