"""ORM models. Importing this package registers every table on `Base.metadata`."""

from ai_restaurant_ordering_assistant.models.base import Base
from ai_restaurant_ordering_assistant.models.chat import (
    ChatMessage,
    ChatSession,
    PendingAction,
    ToolRun,
)
from ai_restaurant_ordering_assistant.models.document import Document
from ai_restaurant_ordering_assistant.models.menu_item import MenuItem
from ai_restaurant_ordering_assistant.models.order import Order, OrderLine
from ai_restaurant_ordering_assistant.models.user import User

__all__ = [
    "Base",
    "ChatMessage",
    "ChatSession",
    "Document",
    "MenuItem",
    "Order",
    "OrderLine",
    "PendingAction",
    "ToolRun",
    "User",
]
