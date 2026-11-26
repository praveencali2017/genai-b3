"""ORM models. Importing this package registers every table on `Base.metadata`."""

from ai_travel_agent.models.base import Base
from ai_travel_agent.models.booking import Booking, BookingLine
from ai_travel_agent.models.chat import (
    ChatMessage,
    ChatSession,
    PendingAction,
    ToolRun,
)
from ai_travel_agent.models.document import Document
from ai_travel_agent.models.package import Package
from ai_travel_agent.models.user import User

__all__ = [
    "Base",
    "Booking",
    "BookingLine",
    "ChatMessage",
    "ChatSession",
    "Document",
    "Package",
    "PendingAction",
    "ToolRun",
    "User",
]
