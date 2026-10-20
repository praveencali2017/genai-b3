"""Request and response models for the Hotel Room Booking API."""

from enum import StrEnum
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RoomType(StrEnum):
    SINGLE = "Single"
    DOUBLE = "Double"
    DELUXE = "Deluxe"
    SUITE = "Suite"


class BookingStatus(StrEnum):
    BOOKED = "Booked"
    CHECKED_IN = "Checked In"
    CHECKED_OUT = "Checked Out"
    CANCELLED = "Cancelled"


# Limits mirror the `hotel_booking` table columns
# (VARCHAR(100), INT CHECK > 0, DECIMAL(10, 2) CHECK > 0).
CustomerName = Annotated[str, Field(min_length=1, max_length=100)]
RoomNumber = Annotated[int, Field(ge=1)]
Amount = Annotated[float, Field(gt=0, le=99_999_999.99)]


class BookingCreate(BaseModel):
    """Fields a client supplies when creating a booking."""

    model_config = ConfigDict(str_strip_whitespace=True)

    customer_name: CustomerName
    room_number: RoomNumber
    room_type: RoomType
    amount: Amount


class RoomUpdate(BaseModel):
    """A move to a different room number, room type, or both."""

    room_number: RoomNumber | None = None
    room_type: RoomType | None = None

    @model_validator(mode="after")
    def require_a_change(self) -> Self:
        if self.room_number is None and self.room_type is None:
            raise ValueError("Provide at least one of 'room_number' or 'room_type'.")
        return self


class Booking(BookingCreate):
    """A stored booking, including its database-assigned id and status."""

    id: int
    status: BookingStatus


class BookingPage(BaseModel):
    """One page of bookings plus the total, so clients know if more exist."""

    items: list[Booking]
    total: int
    limit: int
    offset: int


class DeleteAllResult(BaseModel):
    deleted: int


# Keeps one prompt well inside a small model's context and the user's budget.
MAX_CHAT_MESSAGE_LENGTH = 2000


class ChatRequest(BaseModel):
    """One prompt from the chat widget."""

    model_config = ConfigDict(str_strip_whitespace=True)

    message: Annotated[str, Field(min_length=1, max_length=MAX_CHAT_MESSAGE_LENGTH)]


class ChatReply(BaseModel):
    reply: str
    # The model that produced the reply, e.g. "llama3.2" or "gpt-5.4-mini".
    model: str
