"""Hotel Room Booking System as a REST API backed by MySQL.

Run from this folder (its own uv project):
    uv run fastapi dev main.py
Then open http://127.0.0.1:8000/ for the web UI or /docs for the API.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.responses import FileResponse

from lifecycle import (
    ACTIVE_STATUSES,
    CANCEL,
    CHECK_IN,
    CHECK_OUT,
    StatusTransition,
    conflict_detail,
    room_change_conflict_detail,
)
from repository import BookingRepository, connect, get_repository
from schemas import (
    Booking,
    BookingCreate,
    BookingPage,
    DeleteAllResult,
    RoomUpdate,
)

DEFAULT_PAGE_LIMIT = 10
MAX_PAGE_LIMIT = 100
INDEX_PAGE = Path(__file__).parent / "static" / "index.html"


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
    # Creating the table at startup lets the app run against an empty
    # database, and makes a bad .env or unreachable MySQL fail immediately
    # rather than on the first request.
    connection = connect()
    try:
        BookingRepository(connection).create_table()
    finally:
        connection.close()
    yield


app = FastAPI(title="Hotel Room Booking System", lifespan=lifespan)

Repository = Annotated[BookingRepository, Depends(get_repository)]


def _not_found(booking_id: int) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Booking {booking_id} not found.",
    )


def _conflict(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


def _get_or_404(booking_id: int, repository: BookingRepository) -> Booking:
    booking = repository.get(booking_id)
    if booking is None:
        raise _not_found(booking_id)
    return booking


def _apply_transition(
    transition: StatusTransition, booking_id: int, repository: BookingRepository
) -> Booking:
    # Write first, then read: the write's WHERE clause is the real check, so
    # the read only explains a refusal and never races with the decision.
    changed = repository.change_status(booking_id, transition)
    booking = _get_or_404(booking_id, repository)
    if not changed:
        raise _conflict(conflict_detail(transition, booking.status))
    return booking


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    """Serve the single-page web UI; it calls the /bookings API on this origin."""
    return FileResponse(INDEX_PAGE)


@app.post("/bookings", status_code=status.HTTP_201_CREATED)
def create_booking(booking: BookingCreate, repository: Repository) -> Booking:
    return repository.add(booking)


@app.get("/bookings")
def list_bookings(
    repository: Repository,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_LIMIT)] = DEFAULT_PAGE_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> BookingPage:
    return BookingPage(
        items=repository.list_page(limit=limit, offset=offset),
        total=repository.count(),
        limit=limit,
        offset=offset,
    )


@app.get("/bookings/{id}")
def get_booking(id: int, repository: Repository) -> Booking:
    return _get_or_404(id, repository)


@app.patch("/bookings/{id}/room")
def update_room(id: int, changes: RoomUpdate, repository: Repository) -> Booking:
    repository.update_room(id, changes)
    booking = _get_or_404(id, repository)
    # No row changed and the booking is still active means the values were
    # already set; that's a successful no-op, not an error.
    if booking.status not in ACTIVE_STATUSES:
        raise _conflict(room_change_conflict_detail(booking.status))
    return booking


@app.post("/bookings/{id}/check-in")
def check_in(id: int, repository: Repository) -> Booking:
    return _apply_transition(CHECK_IN, id, repository)


@app.post("/bookings/{id}/check-out")
def check_out(id: int, repository: Repository) -> Booking:
    return _apply_transition(CHECK_OUT, id, repository)


@app.post("/bookings/{id}/cancel")
def cancel_booking(id: int, repository: Repository) -> Booking:
    return _apply_transition(CANCEL, id, repository)


@app.delete("/bookings")
def delete_all_bookings(
    repository: Repository,
    confirm: Annotated[bool, Query()] = False,
) -> DeleteAllResult:
    # A bare DELETE on the collection must not wipe every booking by accident.
    if not confirm:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Deleting all bookings requires ?confirm=true.",
        )
    return DeleteAllResult(deleted=repository.delete_all())
