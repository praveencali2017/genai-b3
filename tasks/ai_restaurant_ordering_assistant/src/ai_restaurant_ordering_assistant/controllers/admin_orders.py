"""Admin order endpoints: see every order and move it through its lifecycle."""

from typing import Annotated

from fastapi import APIRouter, Path

from ai_restaurant_ordering_assistant.controllers.dependencies import DbSession
from ai_restaurant_ordering_assistant.controllers.responses import (
    ADMIN_ERRORS,
    CONFLICT,
    NOT_FOUND,
)
from ai_restaurant_ordering_assistant.models.enums import OrderStatus
from ai_restaurant_ordering_assistant.schemas.order import (
    OrderResponse,
    OrderStatusUpdate,
)
from ai_restaurant_ordering_assistant.security.basic_auth import AdminUser
from ai_restaurant_ordering_assistant.services.order_service import OrderService

router = APIRouter(prefix="/api/admin/orders", tags=["admin"], responses=ADMIN_ERRORS)


@router.get("")
def list_all_orders(
    session: DbSession, _admin: AdminUser, status: OrderStatus | None = None
) -> list[OrderResponse]:
    """Every customer's orders, newest first, optionally of one status."""
    return [OrderResponse.from_order(o) for o in OrderService(session).list_all(status)]


@router.patch("/{order_id}/status", responses={**NOT_FOUND, **CONFLICT})
def change_order_status(
    order_id: Annotated[int, Path(gt=0)],
    update: OrderStatusUpdate,
    session: DbSession,
    _admin: AdminUser,
) -> OrderResponse:
    """Allowed: CONFIRMED → PREPARING or CANCELLED, PREPARING → OUT_FOR_DELIVERY,
    OUT_FOR_DELIVERY → DELIVERED."""
    order = OrderService(session).change_status(order_id, update.status)
    return OrderResponse.from_order(order)
