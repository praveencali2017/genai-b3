"""Traveller booking endpoints. Every route acts only on the caller's own
bookings (admins may also read any booking)."""

from typing import Annotated

from fastapi import APIRouter, Header, Path, Response, status

from ai_travel_agent.controllers.dependencies import DbSession
from ai_travel_agent.controllers.responses import (
    AUTH_ERRORS,
    CONFLICT,
    NOT_FOUND,
)
from ai_travel_agent.models import Booking
from ai_travel_agent.schemas.booking import (
    BookingCreate,
    BookingResponse,
    IdempotencyKey,
)
from ai_travel_agent.security.basic_auth import CurrentUser
from ai_travel_agent.services.booking_service import BookingService

router = APIRouter(prefix="/api/bookings", tags=["bookings"], responses=AUTH_ERRORS)

BookingId = Annotated[int, Path(gt=0)]

REPLAYED_HEADER = "Idempotent-Replayed"


def to_response(service: BookingService, booking: Booking) -> BookingResponse:
    return BookingResponse.from_booking(booking, service.cancellation_quote(booking))


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    responses={
        200: {"description": "Repeat of an earlier request; nothing was created"},
        **CONFLICT,
        **NOT_FOUND,
    },
)
def create_booking(
    request: BookingCreate,
    idempotency_key: Annotated[
        IdempotencyKey,
        Header(
            alias="Idempotency-Key",
            description="A new unique value per booking (e.g. a UUID). Retrying "
            "with the same key returns the original booking instead of a new one.",
        ),
    ],
    session: DbSession,
    user: CurrentUser,
    response: Response,
) -> BookingResponse:
    """Book seats. Prices come from the catalog at this moment."""
    service = BookingService(session)
    placed = service.place(user, request, idempotency_key)
    if not placed.created:
        response.status_code = status.HTTP_200_OK
        response.headers[REPLAYED_HEADER] = "true"
    return to_response(service, placed.booking)


@router.get("")
def list_my_bookings(session: DbSession, user: CurrentUser) -> list[BookingResponse]:
    """The caller's bookings, newest first."""
    service = BookingService(session)
    return [to_response(service, booking) for booking in service.list_for(user)]


@router.get("/{booking_id}", responses=NOT_FOUND)
def get_booking(
    booking_id: BookingId, session: DbSession, user: CurrentUser
) -> BookingResponse:
    """Status and details, with what cancelling today would refund."""
    service = BookingService(session)
    return to_response(service, service.get_visible(user, booking_id))


@router.post("/{booking_id}/cancel", responses={**NOT_FOUND, **CONFLICT})
def cancel_booking(
    booking_id: BookingId, session: DbSession, user: CurrentUser
) -> BookingResponse:
    """Cancel your own booking before departure. Seats are released and the
    refund follows the cancellation policy (100% at 7+ days, 50% at 2-6 days,
    nothing within 2 days)."""
    service = BookingService(session)
    return to_response(service, service.cancel(user, booking_id))
