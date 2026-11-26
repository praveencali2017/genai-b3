"""Admin booking endpoints: see every booking and change its status."""

from typing import Annotated

from fastapi import APIRouter, Path

from ai_travel_agent.controllers.bookings import to_response
from ai_travel_agent.controllers.dependencies import DbSession
from ai_travel_agent.controllers.responses import (
    ADMIN_ERRORS,
    CONFLICT,
    NOT_FOUND,
)
from ai_travel_agent.models.enums import BookingStatus
from ai_travel_agent.schemas.booking import (
    BookingResponse,
    BookingStatusUpdate,
)
from ai_travel_agent.security.basic_auth import AdminUser
from ai_travel_agent.services.booking_service import BookingService

router = APIRouter(prefix="/api/admin/bookings", tags=["admin"], responses=ADMIN_ERRORS)


@router.get("")
def list_all_bookings(
    session: DbSession, _admin: AdminUser, status: BookingStatus | None = None
) -> list[BookingResponse]:
    """Every traveller's bookings, newest first, optionally of one status."""
    service = BookingService(session)
    return [to_response(service, booking) for booking in service.list_all(status)]


@router.patch("/{booking_id}/status", responses={**NOT_FOUND, **CONFLICT})
def change_booking_status(
    booking_id: Annotated[int, Path(gt=0)],
    update: BookingStatusUpdate,
    session: DbSession,
    _admin: AdminUser,
) -> BookingResponse:
    """Allowed: CONFIRMED → COMPLETED, or CONFIRMED → CANCELLED (an operator
    cancellation, refunded in full)."""
    service = BookingService(session)
    return to_response(service, service.change_status(booking_id, update.status))
