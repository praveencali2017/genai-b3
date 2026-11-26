from datetime import date, timedelta
from decimal import Decimal

import pytest

from ai_travel_agent.models import BookingLine, Package
from ai_travel_agent.services.cancellation_policy import (
    operator_cancellation,
    percent_of,
    quote_cancellation,
    refund_percent,
)

TODAY = date(2026, 3, 10)


def _line(package_id: int, departs_in_days: int, unit_price: str, quantity: int):
    package = Package(id=package_id, start_date=TODAY + timedelta(days=departs_in_days))
    return BookingLine(
        package=package,
        package_id=package_id,
        quantity=quantity,
        unit_price=Decimal(unit_price),
    )


@pytest.mark.parametrize(
    ("days", "percent"),
    [(30, 100), (7, 100), (6, 50), (2, 50), (1, 0), (0, 0), (-3, 0)],
)
def test_refund_tiers_match_the_published_policy(days: int, percent: int) -> None:
    assert refund_percent(days) == percent


def test_percent_of_rounds_half_up_to_the_cent() -> None:
    assert percent_of(Decimal("101.01"), 50) == Decimal("50.51")
    assert percent_of(Decimal("24000"), 100) == Decimal("24000.00")
    assert percent_of(Decimal("24000"), 0) == Decimal("0.00")


def test_quote_uses_each_lines_own_departure() -> None:
    quote = quote_cancellation(
        [_line(1, 10, "12000", 2), _line(2, 4, "7500", 1)], TODAY
    )

    assert [line.refund_percent for line in quote.lines] == [100, 50]
    assert [line.days_before_departure for line in quote.lines] == [10, 4]
    assert quote.refund_amount == Decimal("24000.00") + Decimal("3750.00")


def test_operator_cancellation_refunds_in_full_even_on_the_last_day() -> None:
    quote = operator_cancellation([_line(1, 1, "14000", 3)], TODAY)

    assert quote.lines[0].refund_percent == 100
    assert quote.refund_amount == Decimal("42000.00")
