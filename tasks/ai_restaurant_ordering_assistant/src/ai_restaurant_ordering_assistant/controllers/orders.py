"""Customer order endpoints. Every route acts only on the caller's own orders
(admins may also read any order)."""

from typing import Annotated

from fastapi import APIRouter, Header, Path, Response, status

from ai_restaurant_ordering_assistant.controllers.dependencies import DbSession
from ai_restaurant_ordering_assistant.controllers.responses import (
    AUTH_ERRORS,
    CONFLICT,
    NOT_FOUND,
)
from ai_restaurant_ordering_assistant.schemas.order import (
    IdempotencyKey,
    OrderCreate,
    OrderResponse,
)
from ai_restaurant_ordering_assistant.security.basic_auth import CurrentUser
from ai_restaurant_ordering_assistant.services.order_service import OrderService

router = APIRouter(prefix="/api/orders", tags=["orders"], responses=AUTH_ERRORS)

OrderId = Annotated[int, Path(gt=0)]

REPLAYED_HEADER = "Idempotent-Replayed"


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    responses={
        200: {"description": "Repeat of an earlier request; nothing was created"},
        **CONFLICT,
        **NOT_FOUND,
    },
)
def place_order(
    request: OrderCreate,
    idempotency_key: Annotated[
        IdempotencyKey,
        Header(
            alias="Idempotency-Key",
            description="A new unique value per order (e.g. a UUID). Retrying "
            "with the same key returns the original order instead of a new one.",
        ),
    ],
    session: DbSession,
    user: CurrentUser,
    response: Response,
) -> OrderResponse:
    """Place an order. Prices come from the menu at this moment."""
    placed = OrderService(session).place(user, request, idempotency_key)
    if not placed.created:
        response.status_code = status.HTTP_200_OK
        response.headers[REPLAYED_HEADER] = "true"
    return OrderResponse.from_order(placed.order)


@router.get("")
def list_my_orders(session: DbSession, user: CurrentUser) -> list[OrderResponse]:
    """The caller's orders, newest first."""
    return [OrderResponse.from_order(o) for o in OrderService(session).list_for(user)]


@router.get("/{order_id}", responses=NOT_FOUND)
def get_order(
    order_id: OrderId, session: DbSession, user: CurrentUser
) -> OrderResponse:
    return OrderResponse.from_order(OrderService(session).get_visible(user, order_id))


@router.post("/{order_id}/cancel", responses={**NOT_FOUND, **CONFLICT})
def cancel_order(
    order_id: OrderId, session: DbSession, user: CurrentUser
) -> OrderResponse:
    """Cancel your own order before preparation starts; stock is restored."""
    return OrderResponse.from_order(OrderService(session).cancel(user, order_id))
