"""Policy document response bodies."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from ai_restaurant_ordering_assistant.models.enums import DocumentStatus


class DocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    filename: str
    version: int
    status: DocumentStatus
    chunk_count: int
    uploaded_by: int
    created_at: datetime
