"""Order request and response bodies."""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    PositiveInt,
    StringConstraints,
)

from ai_restaurant_ordering_assistant.models import Order, OrderLine
from ai_restaurant_ordering_assistant.models.enums import OrderStatus

MAX_LINES_PER_ORDER = 20
MAX_QUANTITY_PER_LINE = 10

IdempotencyKey = Annotated[
    str,
    StringConstraints(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._:-]+$"),
]


class OrderLineCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    item_id: PositiveInt
    quantity: Annotated[int, Field(ge=1, le=MAX_QUANTITY_PER_LINE)]


def _distinct_items(lines: list[OrderLineCreate]) -> list[OrderLineCreate]:
    item_ids = [line.item_id for line in lines]
    if len(item_ids) != len(set(item_ids)):
        raise ValueError("each item may appear only once; combine the quantities")
    return lines


class OrderCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lines: Annotated[
        list[OrderLineCreate],
        Field(min_length=1, max_length=MAX_LINES_PER_ORDER),
        AfterValidator(_distinct_items),
    ]

    def quantities(self) -> dict[int, int]:
        """Quantity per item id, the order's identity for idempotent replays."""
        return {line.item_id: line.quantity for line in self.lines}


class OrderStatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: OrderStatus


class OrderLineResponse(BaseModel):
    item_id: int
    title: str
    quantity: int
    unit_price: Decimal
    line_total: Decimal

    @classmethod
    def from_line(cls, line: OrderLine) -> Self:
        return cls(
            item_id=line.item_id,
            title=line.item.title,
            quantity=line.quantity,
            unit_price=line.unit_price,
            line_total=line.line_total,
        )


class OrderResponse(BaseModel):
    id: int
    user_id: int
    status: OrderStatus
    total: Decimal
    created_at: datetime
    updated_at: datetime
    lines: list[OrderLineResponse]

    @classmethod
    def from_order(cls, order: Order) -> Self:
        return cls(
            id=order.id,
            user_id=order.user_id,
            status=order.status,
            total=order.total,
            created_at=order.created_at,
            updated_at=order.updated_at,
            lines=[OrderLineResponse.from_line(line) for line in order.lines],
        )
