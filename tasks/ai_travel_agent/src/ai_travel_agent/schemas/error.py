from typing import Any

from pydantic import BaseModel, Field


class ErrorResponse(BaseModel):
    """The body of every error response."""

    code: str = Field(examples=["INSUFFICIENT_CAPACITY"])
    message: str
    request_id: str
    details: list[dict[str, Any]] | None = Field(
        default=None, description="Field-level problems, for validation errors."
    )
