"""Booking rules: seats, departure dates, price snapshots, idempotency,
ownership, cancellation refunds and status changes.

Concurrency: creating or cancelling a booking locks the affected package rows
(`SELECT ... FOR UPDATE`, in id order), rechecks seats under the lock, and
changes seats and booking in the caller's single transaction. Two requests for
the last seat therefore serialize, and only the first succeeds.

Idempotency: a booking is identified by `(user, Idempotency-Key)`. A repeat
with the same key and the same lines returns the existing booking instead of
creating another; the same key with different lines is a conflict. Because the
key is looked up only after the package locks are held (and the database runs
at READ COMMITTED), a concurrent duplicate waits for the first request and then
finds its booking. Two duplicates with *different* lines don't share locks; the
unique constraint catches the second, which is then answered the same way.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ai_travel_agent.exceptions.errors import (
    DepartureInPastError,
    IdempotencyConflictError,
    InsufficientCapacityError,
    InvalidStatusTransitionError,
    PackageUnavailableError,
    ResourceNotFoundError,
)
from ai_travel_agent.models import Booking, BookingLine, Package, User
from ai_travel_agent.models.base import utc_now
from ai_travel_agent.models.enums import BookingStatus
from ai_travel_agent.repositories.booking_repository import BookingRepository
from ai_travel_agent.repositories.package_repository import PackageRepository
from ai_travel_agent.schemas.booking import BookingCreate
from ai_travel_agent.services.cancellation_policy import (
    CancellationQuote,
    operator_cancellation,
    quote_cancellation,
)
from ai_travel_agent.services.catalog_service import PACKAGE

BOOKING = "Booking"

# Admin status changes. Travellers can only cancel, and only before departure.
ALLOWED_TRANSITIONS: dict[BookingStatus, frozenset[BookingStatus]] = {
    BookingStatus.CONFIRMED: frozenset(
        {BookingStatus.COMPLETED, BookingStatus.CANCELLED}
    ),
    BookingStatus.COMPLETED: frozenset(),
    BookingStatus.CANCELLED: frozenset(),
}


@dataclass(frozen=True, slots=True)
class PlacedBooking:
    booking: Booking
    # False when the request repeated an earlier one and nothing was created.
    created: bool


class BookingService:
    def __init__(
        self, session: Session, *, today: Callable[[], date] = date.today
    ) -> None:
        self._session = session
        self._bookings = BookingRepository(session)
        self._packages = PackageRepository(session)
        self._today = today

    # ------------------------------------------------------------ reads

    def list_for(self, user: User) -> Sequence[Booking]:
        return self._bookings.list_for_user(user.id)

    def list_all(self, status: BookingStatus | None = None) -> Sequence[Booking]:
        return self._bookings.list_all(status)

    def get_visible(self, user: User, booking_id: int) -> Booking:
        """The booking if `user` owns it or is an admin; otherwise not found."""
        booking = self._bookings.get(booking_id)
        if booking is None or not (user.is_admin or booking.user_id == user.id):
            raise ResourceNotFoundError(BOOKING, booking_id)
        return booking

    def cancellation_quote(self, booking: Booking) -> CancellationQuote | None:
        """What the owner would get back by cancelling today, or None if the
        booking can't be cancelled by its owner (any more)."""
        if not self._owner_may_cancel(booking):
            return None
        return quote_cancellation(booking.lines, self._today())

    # ------------------------------------------------------------ writes

    def place(
        self, user: User, request: BookingCreate, idempotency_key: str
    ) -> PlacedBooking:
        quantities = request.quantities()
        packages = self._packages.lock(quantities.keys())

        existing = self._bookings.find_by_idempotency_key(user.id, idempotency_key)
        if existing is not None:
            return self._replay(existing, quantities, idempotency_key)

        _check_bookable(packages, quantities, self._today())
        booking = _new_booking(user, packages, quantities, idempotency_key)
        try:
            # A savepoint, so a duplicate key rolls back only this insert (and
            # the seat changes below) and the replay check can still run.
            with self._session.begin_nested():
                for package_id, quantity in quantities.items():
                    packages[package_id].capacity -= quantity
                self._bookings.add(booking)
        except IntegrityError:
            existing = self._bookings.find_by_idempotency_key(user.id, idempotency_key)
            if existing is None:
                raise
            return self._replay(existing, quantities, idempotency_key)
        return PlacedBooking(booking, created=True)

    def cancel(self, user: User, booking_id: int) -> Booking:
        """Cancel the caller's own booking before departure; the refund
        follows the cancellation policy."""
        booking = self._bookings.lock(booking_id)
        if booking is None or booking.user_id != user.id:
            raise ResourceNotFoundError(BOOKING, booking_id)
        self._require_transition(booking, BookingStatus.CANCELLED)
        today = self._today()
        departed = _first_departed(booking, today)
        if departed is not None:
            raise DepartureInPastError(
                departed.id, departed.title, departed.start_date.isoformat()
            )
        self._cancel(booking, quote_cancellation(booking.lines, today))
        return booking

    def change_status(self, booking_id: int, target: BookingStatus) -> Booking:
        """Admin: complete a booking, or cancel it with a full refund."""
        booking = self._bookings.lock(booking_id)
        if booking is None:
            raise ResourceNotFoundError(BOOKING, booking_id)
        self._require_transition(booking, target)
        if target is BookingStatus.CANCELLED:
            self._cancel(booking, operator_cancellation(booking.lines, self._today()))
        else:
            booking.status = target
        return booking

    # ------------------------------------------------------------ internals

    def _replay(
        self, existing: Booking, quantities: dict[int, int], idempotency_key: str
    ) -> PlacedBooking:
        if _quantities_of(existing) != quantities:
            raise IdempotencyConflictError(idempotency_key)
        return PlacedBooking(existing, created=False)

    def _owner_may_cancel(self, booking: Booking) -> bool:
        return (
            BookingStatus.CANCELLED in ALLOWED_TRANSITIONS[booking.status]
            and _first_departed(booking, self._today()) is None
        )

    def _cancel(self, booking: Booking, quote: CancellationQuote) -> None:
        # The status guard (only CONFIRMED bookings can be cancelled, and the
        # booking row is locked) makes this run at most once per booking, so
        # seats are restored exactly once.
        packages = self._packages.lock([line.package_id for line in booking.lines])
        for line in booking.lines:
            packages[line.package_id].capacity += line.quantity
        booking.status = BookingStatus.CANCELLED
        booking.refund_amount = quote.refund_amount
        booking.cancelled_at = utc_now()

    @staticmethod
    def _require_transition(booking: Booking, target: BookingStatus) -> None:
        if target not in ALLOWED_TRANSITIONS[booking.status]:
            raise InvalidStatusTransitionError(booking.id, booking.status, target)


def _check_bookable(
    packages: dict[int, Package], quantities: dict[int, int], today: date
) -> None:
    """Raise for the first line that can't be booked; changes nothing."""
    for package_id, quantity in sorted(quantities.items()):
        package = packages.get(package_id)
        if package is None:
            raise ResourceNotFoundError(PACKAGE, package_id)
        if not package.active:
            raise PackageUnavailableError(package_id, package.title)
        if package.start_date <= today:
            raise DepartureInPastError(
                package_id, package.title, package.start_date.isoformat()
            )
        if quantity > package.capacity:
            raise InsufficientCapacityError(
                package_id,
                package.title,
                requested=quantity,
                available=package.capacity,
            )


def _first_departed(booking: Booking, today: date) -> Package | None:
    """The first package in the booking that has departed (or departs today)."""
    for line in booking.lines:
        if line.package.start_date <= today:
            return line.package
    return None


def _new_booking(
    user: User,
    packages: dict[int, Package],
    quantities: dict[int, int],
    idempotency_key: str,
) -> Booking:
    lines = [
        # Price comes from the locked row, never from the client or the model.
        BookingLine(
            package=packages[package_id],
            quantity=quantity,
            unit_price=packages[package_id].price,
        )
        for package_id, quantity in quantities.items()
    ]
    total = sum((line.line_total for line in lines), start=Decimal("0"))
    return Booking(
        user_id=user.id,
        status=BookingStatus.CONFIRMED,
        total=total,
        idempotency_key=idempotency_key,
        lines=lines,
    )


def _quantities_of(booking: Booking) -> dict[int, int]:
    return {line.package_id: line.quantity for line in booking.lines}
