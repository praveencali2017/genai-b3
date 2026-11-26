"""Catalog (tour package) request and response bodies."""

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeInt,
    StringConstraints,
    model_validator,
)

from ai_travel_agent.models import Package

MAX_DURATION_DAYS = 60

Title = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=3, max_length=120)
]
Description = Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)]
Destination = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=2, max_length=80)
]
Price = Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=2)]
DurationDays = Annotated[int, Field(ge=1, le=MAX_DURATION_DAYS)]


def _in_future(start_date: date) -> date:
    if start_date <= date.today():
        raise ValueError("the departure date must be in the future")
    return start_date


FutureDate = Annotated[date, AfterValidator(_in_future)]


class PackageCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Title
    destination: Destination
    description: Description | None = None
    duration_days: DurationDays
    price: Price = Field(description="Price per traveller.")
    capacity: NonNegativeInt = Field(description="Seats available.")
    start_date: FutureDate = Field(description="Departure date.")


class PackageUpdate(BaseModel):
    """Fields to change; omitted fields keep their value."""

    model_config = ConfigDict(extra="forbid")

    title: Title | None = None
    destination: Destination | None = None
    description: Description | None = None
    duration_days: DurationDays | None = None
    price: Price | None = None
    capacity: NonNegativeInt | None = None
    start_date: FutureDate | None = None
    active: bool | None = Field(
        default=None, description="Set true to re-enable a disabled package."
    )

    @model_validator(mode="after")
    def _no_null_for_required_columns(self) -> Self:
        # `description` may be cleared with null; the others can't be null.
        nulled = [
            name
            for name in self.model_fields_set - {"description"}
            if getattr(self, name) is None
        ]
        if nulled:
            raise ValueError(f"these fields can't be null: {', '.join(nulled)}")
        return self


class PackageResponse(BaseModel):
    id: int
    title: str
    destination: str
    description: str | None
    duration_days: int
    price: Decimal = Field(description="Price per traveller.")
    capacity: int = Field(description="Seats available right now.")
    start_date: date = Field(description="Departure date.")
    end_date: date = Field(description="Last day of the trip.")
    active: bool
    updated_at: datetime

    @classmethod
    def from_package(cls, package: Package) -> Self:
        return cls(
            id=package.id,
            title=package.title,
            destination=package.destination,
            description=package.description,
            duration_days=package.duration_days,
            price=package.price,
            capacity=package.capacity,
            start_date=package.start_date,
            end_date=package.start_date + timedelta(days=package.duration_days - 1),
            active=package.active,
            updated_at=package.updated_at,
        )
