"""The cancellation policy: how much of a booking is refunded, and when.

This module is the single source of truth for the refund tiers. The booking
service applies it when a booking is cancelled, booking responses show a quote
from it, and the chatbot reads that quote through the API, so the rule can't
drift between the UI, the API and the agent. The policy document in
`knowledge_base/` states the same tiers in prose.

Pure functions only: no database, no clock (callers pass `today`).
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from ai_travel_agent.models import BookingLine

# (minimum whole days before departure, percent refunded), most generous first.
REFUND_TIERS: tuple[tuple[int, int], ...] = ((7, 100), (2, 50), (0, 0))

# When the operator (an admin) cancels, the traveller gets everything back.
OPERATOR_CANCELLATION_PERCENT = 100

_CENT = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class LineRefund:
    package_id: int
    days_before_departure: int
    refund_percent: int
    refund_amount: Decimal


@dataclass(frozen=True, slots=True)
class CancellationQuote:
    lines: tuple[LineRefund, ...]

    @property
    def refund_amount(self) -> Decimal:
        return sum((line.refund_amount for line in self.lines), start=Decimal("0"))


def refund_percent(days_before_departure: int) -> int:
    """Percent refunded when cancelling this many days before departure."""
    for minimum_days, percent in REFUND_TIERS:
        if days_before_departure >= minimum_days:
            return percent
    return 0


def percent_of(amount: Decimal, percent: int) -> Decimal:
    return (amount * percent / 100).quantize(_CENT, rounding=ROUND_HALF_UP)


def quote_cancellation(lines: Iterable[BookingLine], today: date) -> CancellationQuote:
    """The refund each line would get if the booking were cancelled `today`."""
    refunds = []
    for line in lines:
        days = (line.package.start_date - today).days
        percent = refund_percent(days)
        refunds.append(
            LineRefund(
                package_id=line.package_id,
                days_before_departure=days,
                refund_percent=percent,
                refund_amount=percent_of(line.line_total, percent),
            )
        )
    return CancellationQuote(tuple(refunds))


def operator_cancellation(
    lines: Iterable[BookingLine], today: date
) -> CancellationQuote:
    """The refund when the operator cancels: the full amount, whatever the date."""
    return CancellationQuote(
        tuple(
            LineRefund(
                package_id=line.package_id,
                days_before_departure=(line.package.start_date - today).days,
                refund_percent=OPERATOR_CANCELLATION_PERCENT,
                refund_amount=percent_of(
                    line.line_total, OPERATOR_CANCELLATION_PERCENT
                ),
            )
            for line in lines
        )
    )
