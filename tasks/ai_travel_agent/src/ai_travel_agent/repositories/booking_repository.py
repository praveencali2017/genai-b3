from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from ai_travel_agent.models import Booking
from ai_travel_agent.models.enums import BookingStatus


class BookingRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, booking_id: int) -> Booking | None:
        return self._session.get(Booking, booking_id)

    def lock(self, booking_id: int) -> Booking | None:
        """`SELECT ... FOR UPDATE` one booking, replacing any cached copy."""
        return self._session.scalars(
            select(Booking)
            .where(Booking.id == booking_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).one_or_none()

    def find_by_idempotency_key(self, user_id: int, key: str) -> Booking | None:
        return self._session.scalars(
            select(Booking)
            .where(Booking.user_id == user_id, Booking.idempotency_key == key)
            .execution_options(populate_existing=True)
        ).one_or_none()

    def list_for_user(self, user_id: int) -> Sequence[Booking]:
        return self._session.scalars(
            select(Booking)
            .where(Booking.user_id == user_id)
            .order_by(Booking.id.desc())
        ).all()

    def list_all(self, status: BookingStatus | None = None) -> Sequence[Booking]:
        statement = select(Booking).order_by(Booking.id.desc())
        if status is not None:
            statement = statement.where(Booking.status == status)
        return self._session.scalars(statement).all()

    def add(self, booking: Booking) -> Booking:
        self._session.add(booking)
        self._session.flush()
        return booking
