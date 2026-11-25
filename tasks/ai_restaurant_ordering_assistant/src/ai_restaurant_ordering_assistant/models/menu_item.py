from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, Index, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from ai_restaurant_ordering_assistant.models.base import Base, Timestamp, utc_now

Money = Numeric(12, 2)


class MenuItem(Base):
    """A dish on the menu. `capacity` is the number of portions still available.

    Items referenced by orders are never deleted; disabling sets `active` false.
    """

    __tablename__ = "menu_items"
    __table_args__ = (
        CheckConstraint("price > 0", name="price_positive"),
        CheckConstraint("capacity >= 0", name="capacity_non_negative"),
        Index(None, "active", "category"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(String(500))
    category: Mapped[str] = mapped_column(String(50))
    vegetarian: Mapped[bool]
    price: Mapped[Decimal] = mapped_column(Money)
    capacity: Mapped[int]
    active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(Timestamp, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        Timestamp, default=utc_now, onupdate=utc_now
    )
