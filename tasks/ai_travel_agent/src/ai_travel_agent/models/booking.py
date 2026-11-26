from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ai_travel_agent.models.base import (
    Base,
    Money,
    Timestamp,
    enum_column,
    utc_now,
)
from ai_travel_agent.models.enums import BookingStatus
from ai_travel_agent.models.package import Package


class Booking(Base):
    """A traveller's booking. `(user_id, idempotency_key)` is unique, so a
    retried request with the same key finds the booking instead of creating a
    second.

    `refund_amount` and `cancelled_at` are set once, when the booking is
    cancelled, from the cancellation policy in force on that day.
    """

    __tablename__ = "bookings"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key"),
        CheckConstraint("total >= 0", name="total_non_negative"),
        CheckConstraint("refund_amount >= 0", name="refund_non_negative"),
        Index(None, "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[BookingStatus] = mapped_column(
        enum_column(BookingStatus), default=BookingStatus.CONFIRMED
    )
    total: Mapped[Decimal] = mapped_column(Money)
    refund_amount: Mapped[Decimal | None] = mapped_column(Money)
    cancelled_at: Mapped[datetime | None] = mapped_column(Timestamp)
    idempotency_key: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(Timestamp, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        Timestamp, default=utc_now, onupdate=utc_now
    )

    lines: Mapped[list["BookingLine"]] = relationship(
        back_populates="booking",
        cascade="all, delete-orphan",
        order_by="BookingLine.id",
        lazy="selectin",
    )


class BookingLine(Base):
    """Seats on one package, with the price per traveller at booking time (a
    snapshot that later price changes don't touch)."""

    __tablename__ = "booking_lines"
    __table_args__ = (
        CheckConstraint("quantity BETWEEN 1 AND 10", name="quantity_range"),
        CheckConstraint("unit_price > 0", name="unit_price_positive"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    booking_id: Mapped[int] = mapped_column(
        ForeignKey("bookings.id", ondelete="CASCADE"), index=True
    )
    package_id: Mapped[int] = mapped_column(ForeignKey("packages.id"), index=True)
    quantity: Mapped[int]
    unit_price: Mapped[Decimal] = mapped_column(Money)

    booking: Mapped[Booking] = relationship(back_populates="lines")
    package: Mapped[Package] = relationship(lazy="joined")

    @property
    def line_total(self) -> Decimal:
        return self.unit_price * self.quantity
