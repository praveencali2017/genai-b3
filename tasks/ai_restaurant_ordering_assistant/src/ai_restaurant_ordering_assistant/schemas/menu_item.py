"""Catalog request and response bodies."""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeInt,
    StringConstraints,
    model_validator,
)

from ai_restaurant_ordering_assistant.models import MenuItem

Title = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=3, max_length=120)
]
Description = Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)]
Category = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=2, max_length=50)
]
Price = Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=2)]


class ItemCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Title
    description: Description | None = None
    category: Category
    vegetarian: bool
    price: Price
    capacity: NonNegativeInt = Field(description="Portions available.")


class ItemUpdate(BaseModel):
    """Fields to change; omitted fields keep their value."""

    model_config = ConfigDict(extra="forbid")

    title: Title | None = None
    description: Description | None = None
    category: Category | None = None
    vegetarian: bool | None = None
    price: Price | None = None
    capacity: NonNegativeInt | None = None
    active: bool | None = Field(
        default=None, description="Set true to re-enable a disabled item."
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


class ItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    description: str | None
    category: str
    vegetarian: bool
    price: Decimal
    capacity: int = Field(description="Portions available right now.")
    active: bool
    updated_at: datetime

    @classmethod
    def from_item(cls, item: MenuItem) -> Self:
        return cls.model_validate(item)
