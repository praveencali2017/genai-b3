"""Order rules: stock, price snapshots, idempotency, ownership and status changes.

Concurrency: creating or cancelling an order locks the affected menu item rows
(`SELECT ... FOR UPDATE`, in id order), rechecks stock under the lock, and
changes stock and order in the caller's single transaction. Two requests for
the last portion therefore serialize, and only the first succeeds.

Idempotency: an order is identified by `(user, Idempotency-Key)`. A repeat with
the same key and the same lines returns the existing order instead of creating
another; the same key with different lines is a conflict. Because the key is
looked up only after the item locks are held (and the database runs at READ
COMMITTED), a concurrent duplicate waits for the first request and then finds
its order. Two duplicates with *different* lines don't share locks; the unique
constraint catches the second, which is then answered the same way.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ai_restaurant_ordering_assistant.exceptions.errors import (
    IdempotencyConflictError,
    InsufficientCapacityError,
    InvalidStatusTransitionError,
    ItemUnavailableError,
    ResourceNotFoundError,
)
from ai_restaurant_ordering_assistant.models import MenuItem, Order, OrderLine, User
from ai_restaurant_ordering_assistant.models.enums import OrderStatus
from ai_restaurant_ordering_assistant.repositories.menu_item_repository import (
    MenuItemRepository,
)
from ai_restaurant_ordering_assistant.repositories.order_repository import (
    OrderRepository,
)
from ai_restaurant_ordering_assistant.schemas.order import OrderCreate
from ai_restaurant_ordering_assistant.services.catalog_service import MENU_ITEM

ORDER = "Order"

# Admin status changes. Customers can only cancel, and only before preparation.
ALLOWED_TRANSITIONS: dict[OrderStatus, frozenset[OrderStatus]] = {
    OrderStatus.CONFIRMED: frozenset({OrderStatus.PREPARING, OrderStatus.CANCELLED}),
    OrderStatus.PREPARING: frozenset({OrderStatus.OUT_FOR_DELIVERY}),
    OrderStatus.OUT_FOR_DELIVERY: frozenset({OrderStatus.DELIVERED}),
    OrderStatus.DELIVERED: frozenset(),
    OrderStatus.CANCELLED: frozenset(),
}


@dataclass(frozen=True, slots=True)
class PlacedOrder:
    order: Order
    # False when the request repeated an earlier one and nothing was created.
    created: bool


class OrderService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._orders = OrderRepository(session)
        self._items = MenuItemRepository(session)

    # ------------------------------------------------------------ reads

    def list_for(self, user: User) -> Sequence[Order]:
        return self._orders.list_for_user(user.id)

    def list_all(self, status: OrderStatus | None = None) -> Sequence[Order]:
        return self._orders.list_all(status)

    def get_visible(self, user: User, order_id: int) -> Order:
        """The order if `user` owns it or is an admin; otherwise not found."""
        order = self._orders.get(order_id)
        if order is None or not (user.is_admin or order.user_id == user.id):
            raise ResourceNotFoundError(ORDER, order_id)
        return order

    # ------------------------------------------------------------ writes

    def place(
        self, user: User, request: OrderCreate, idempotency_key: str
    ) -> PlacedOrder:
        quantities = request.quantities()
        items = self._items.lock(quantities.keys())

        existing = self._orders.find_by_idempotency_key(user.id, idempotency_key)
        if existing is not None:
            return self._replay(existing, quantities, idempotency_key)

        _check_stock(items, quantities)
        order = _new_order(user, items, quantities, idempotency_key)
        try:
            # A savepoint, so a duplicate key rolls back only this insert (and
            # the stock changes below) and the replay check can still run.
            with self._session.begin_nested():
                for item_id, quantity in quantities.items():
                    items[item_id].capacity -= quantity
                self._orders.add(order)
        except IntegrityError:
            existing = self._orders.find_by_idempotency_key(user.id, idempotency_key)
            if existing is None:
                raise
            return self._replay(existing, quantities, idempotency_key)
        return PlacedOrder(order, created=True)

    def cancel(self, user: User, order_id: int) -> Order:
        """Cancel the caller's own order; allowed only before preparation."""
        order = self._orders.lock(order_id)
        if order is None or order.user_id != user.id:
            raise ResourceNotFoundError(ORDER, order_id)
        self._transition(order, OrderStatus.CANCELLED)
        return order

    def change_status(self, order_id: int, target: OrderStatus) -> Order:
        """Admin: move an order along its lifecycle (or cancel it)."""
        order = self._orders.lock(order_id)
        if order is None:
            raise ResourceNotFoundError(ORDER, order_id)
        self._transition(order, target)
        return order

    # ------------------------------------------------------------ internals

    def _replay(
        self, existing: Order, quantities: dict[int, int], idempotency_key: str
    ) -> PlacedOrder:
        if _quantities_of(existing) != quantities:
            raise IdempotencyConflictError(idempotency_key)
        return PlacedOrder(existing, created=False)

    def _transition(self, order: Order, target: OrderStatus) -> None:
        if target not in ALLOWED_TRANSITIONS[order.status]:
            raise InvalidStatusTransitionError(order.id, order.status, target)
        if target is OrderStatus.CANCELLED:
            # The status guard (only CONFIRMED orders can be cancelled, and the
            # order row is locked) makes this run at most once per order.
            items = self._items.lock([line.item_id for line in order.lines])
            for line in order.lines:
                items[line.item_id].capacity += line.quantity
        order.status = target


def _check_stock(items: dict[int, MenuItem], quantities: dict[int, int]) -> None:
    """Raise for the first line that can't be served; changes nothing."""
    for item_id, quantity in sorted(quantities.items()):
        item = items.get(item_id)
        if item is None:
            raise ResourceNotFoundError(MENU_ITEM, item_id)
        if not item.active:
            raise ItemUnavailableError(item_id, item.title)
        if quantity > item.capacity:
            raise InsufficientCapacityError(
                item_id, item.title, requested=quantity, available=item.capacity
            )


def _new_order(
    user: User,
    items: dict[int, MenuItem],
    quantities: dict[int, int],
    idempotency_key: str,
) -> Order:
    lines = [
        # Price comes from the locked row, never from the client or the model.
        OrderLine(
            item=items[item_id], quantity=quantity, unit_price=items[item_id].price
        )
        for item_id, quantity in quantities.items()
    ]
    total = sum((line.line_total for line in lines), start=Decimal("0"))
    return Order(
        user_id=user.id,
        status=OrderStatus.CONFIRMED,
        total=total,
        idempotency_key=idempotency_key,
        lines=lines,
    )


def _quantities_of(order: Order) -> dict[int, int]:
    return {line.item_id: line.quantity for line in order.lines}
