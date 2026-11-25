from collections.abc import Collection, Sequence
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from ai_restaurant_ordering_assistant.models import MenuItem


@dataclass(frozen=True, slots=True)
class MenuFilters:
    """Catalog search criteria; None means "don't filter on this"."""

    category: str | None = None
    vegetarian: bool | None = None
    max_price: Decimal | None = None
    keyword: str | None = None
    include_inactive: bool = False


class MenuItemRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def search(self, filters: MenuFilters) -> Sequence[MenuItem]:
        statement = _apply_filters(select(MenuItem), filters)
        return self._session.scalars(
            statement.order_by(MenuItem.category, MenuItem.title)
        ).all()

    def get(self, item_id: int) -> MenuItem | None:
        return self._session.get(MenuItem, item_id)

    def lock(self, item_ids: Collection[int]) -> dict[int, MenuItem]:
        """`SELECT ... FOR UPDATE` the items, in id order so that two
        transactions locking overlapping sets can't deadlock each other.

        Missing ids are simply absent from the result.
        """
        if not item_ids:
            return {}
        items = self._session.scalars(
            select(MenuItem)
            .where(MenuItem.id.in_(item_ids))
            .order_by(MenuItem.id)
            .with_for_update()
            # A row cached in the session from an earlier read must be
            # replaced by the locked, current version.
            .execution_options(populate_existing=True)
        ).all()
        return {item.id: item for item in items}

    def add(self, item: MenuItem) -> MenuItem:
        self._session.add(item)
        self._session.flush()
        return item


def _apply_filters(
    statement: Select[tuple[MenuItem]], filters: MenuFilters
) -> Select[tuple[MenuItem]]:
    if not filters.include_inactive:
        statement = statement.where(MenuItem.active.is_(True))
    if filters.category:
        statement = statement.where(MenuItem.category == filters.category)
    if filters.vegetarian is not None:
        statement = statement.where(MenuItem.vegetarian.is_(filters.vegetarian))
    if filters.max_price is not None:
        statement = statement.where(MenuItem.price <= filters.max_price)
    if filters.keyword:
        pattern = f"%{_escape_like(filters.keyword)}%"
        statement = statement.where(
            MenuItem.title.ilike(pattern, escape="\\")
            | MenuItem.description.ilike(pattern, escape="\\")
            | MenuItem.category.ilike(pattern, escape="\\")
        )
    return statement


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
