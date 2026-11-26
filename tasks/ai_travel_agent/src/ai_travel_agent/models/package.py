from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from ai_travel_agent.models.base import Base, Money, Timestamp, utc_now


class Package(Base):
    """A tour package. `capacity` is the number of seats still available, and
    `start_date` the departure date.

    Packages referenced by bookings are never deleted; disabling sets `active`
    false.
    """

    __tablename__ = "packages"
    __table_args__ = (
        CheckConstraint("price > 0", name="price_positive"),
        CheckConstraint("capacity >= 0", name="capacity_non_negative"),
        CheckConstraint("duration_days > 0", name="duration_positive"),
        Index(None, "active", "destination"),
        Index(None, "start_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(120))
    destination: Mapped[str] = mapped_column(String(80))
    description: Mapped[str | None] = mapped_column(String(500))
    duration_days: Mapped[int]
    price: Mapped[Decimal] = mapped_column(Money)
    capacity: Mapped[int]
    start_date: Mapped[date]
    active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(Timestamp, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        Timestamp, default=utc_now, onupdate=utc_now
    )
