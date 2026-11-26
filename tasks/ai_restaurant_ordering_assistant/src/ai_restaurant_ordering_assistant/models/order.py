from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ai_restaurant_ordering_assistant.models.base import (
    Base,
    Timestamp,
    enum_column,
    utc_now,
)
from ai_restaurant_ordering_assistant.models.enums import OrderStatus
from ai_restaurant_ordering_assistant.models.menu_item import MenuItem, Money


class Order(Base):
    """A customer's order. `(user_id, idempotency_key)` is unique, so a retried
    request with the same key finds the order instead of creating a second."""

    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key"),
        CheckConstraint("total >= 0", name="total_non_negative"),
        Index(None, "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[OrderStatus] = mapped_column(
        enum_column(OrderStatus), default=OrderStatus.CONFIRMED
    )
    total: Mapped[Decimal] = mapped_column(Money)
    idempotency_key: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(Timestamp, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        Timestamp, default=utc_now, onupdate=utc_now
    )

    lines: Mapped[list["OrderLine"]] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
        order_by="OrderLine.id",
        lazy="selectin",
    )


class OrderLine(Base):
    """One dish in an order, with the price it was sold at (a snapshot that
    later menu price changes don't touch)."""

    __tablename__ = "order_lines"
    __table_args__ = (
        CheckConstraint("quantity BETWEEN 1 AND 10", name="quantity_range"),
        CheckConstraint("unit_price > 0", name="unit_price_positive"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), index=True
    )
    item_id: Mapped[int] = mapped_column(ForeignKey("menu_items.id"), index=True)
    quantity: Mapped[int]
    unit_price: Mapped[Decimal] = mapped_column(Money)

    order: Mapped[Order] = relationship(back_populates="lines")
    item: Mapped[MenuItem] = relationship(lazy="joined")

    @property
    def line_total(self) -> Decimal:
        return self.unit_price * self.quantity
