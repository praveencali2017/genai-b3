"""Catalog rules: who sees which items, and how admins change them."""

from collections.abc import Sequence

from sqlalchemy.orm import Session

from ai_restaurant_ordering_assistant.exceptions.errors import ResourceNotFoundError
from ai_restaurant_ordering_assistant.models import MenuItem
from ai_restaurant_ordering_assistant.repositories.menu_item_repository import (
    MenuFilters,
    MenuItemRepository,
)
from ai_restaurant_ordering_assistant.schemas.menu_item import ItemCreate, ItemUpdate

MENU_ITEM = "Menu item"


class CatalogService:
    def __init__(self, session: Session) -> None:
        self._items = MenuItemRepository(session)

    def search(self, filters: MenuFilters) -> Sequence[MenuItem]:
        return self._items.search(filters)

    def get(self, item_id: int, *, include_inactive: bool = False) -> MenuItem:
        """The item; a disabled one counts as missing unless `include_inactive`."""
        item = self._items.get(item_id)
        if item is None or not (item.active or include_inactive):
            raise ResourceNotFoundError(MENU_ITEM, item_id)
        return item

    def create(self, draft: ItemCreate) -> MenuItem:
        return self._items.add(MenuItem(**draft.model_dump(), active=True))

    def update(self, item_id: int, changes: ItemUpdate) -> MenuItem:
        item = self.get(item_id, include_inactive=True)
        for name, value in changes.model_dump(exclude_unset=True).items():
            setattr(item, name, value)
        return item

    def disable(self, item_id: int) -> None:
        """Soft delete: orders keep referring to the item, nobody can buy it."""
        self.get(item_id, include_inactive=True).active = False
