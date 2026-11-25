from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from ai_restaurant_ordering_assistant.models import Order
from ai_restaurant_ordering_assistant.models.enums import OrderStatus


class OrderRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, order_id: int) -> Order | None:
        return self._session.get(Order, order_id)

    def lock(self, order_id: int) -> Order | None:
        """`SELECT ... FOR UPDATE` one order, replacing any cached copy."""
        return self._session.scalars(
            select(Order)
            .where(Order.id == order_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).one_or_none()

    def find_by_idempotency_key(self, user_id: int, key: str) -> Order | None:
        return self._session.scalars(
            select(Order)
            .where(Order.user_id == user_id, Order.idempotency_key == key)
            .execution_options(populate_existing=True)
        ).one_or_none()

    def list_for_user(self, user_id: int) -> Sequence[Order]:
        return self._session.scalars(
            select(Order).where(Order.user_id == user_id).order_by(Order.id.desc())
        ).all()

    def list_all(self, status: OrderStatus | None = None) -> Sequence[Order]:
        statement = select(Order).order_by(Order.id.desc())
        if status is not None:
            statement = statement.where(Order.status == status)
        return self._session.scalars(statement).all()

    def add(self, order: Order) -> Order:
        self._session.add(order)
        self._session.flush()
        return order
