"""Catalog endpoints: public browsing, admin editing."""

from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Path, Query, status

from ai_restaurant_ordering_assistant.controllers.dependencies import DbSession
from ai_restaurant_ordering_assistant.controllers.responses import (
    ADMIN_ERRORS,
    NOT_FOUND,
)
from ai_restaurant_ordering_assistant.exceptions.errors import PermissionDeniedError
from ai_restaurant_ordering_assistant.repositories.menu_item_repository import (
    MenuFilters,
)
from ai_restaurant_ordering_assistant.schemas.menu_item import (
    ItemCreate,
    ItemResponse,
    ItemUpdate,
)
from ai_restaurant_ordering_assistant.security.basic_auth import (
    AdminUser,
    OptionalUser,
)
from ai_restaurant_ordering_assistant.services.catalog_service import CatalogService

router = APIRouter(prefix="/api/menu_items", tags=["menu"])

ItemId = Annotated[int, Path(gt=0)]


@router.get("")
def list_menu_items(
    session: DbSession,
    user: OptionalUser,
    category: Annotated[str | None, Query(max_length=50)] = None,
    vegetarian: bool | None = None,
    max_price: Annotated[Decimal | None, Query(gt=0)] = None,
    keyword: Annotated[str | None, Query(max_length=100)] = None,
    include_inactive: Annotated[
        bool, Query(description="Admins only: also list disabled items.")
    ] = False,
) -> list[ItemResponse]:
    """Active items, filtered; anyone may call this."""
    if include_inactive and not (user and user.is_admin):
        raise PermissionDeniedError("Only administrators can list disabled items.")
    filters = MenuFilters(
        category=category,
        vegetarian=vegetarian,
        max_price=max_price,
        keyword=keyword.strip() if keyword else None,
        include_inactive=include_inactive,
    )
    return [
        ItemResponse.from_item(item) for item in CatalogService(session).search(filters)
    ]


@router.get("/{item_id}", responses=NOT_FOUND)
def get_menu_item(item_id: ItemId, session: DbSession) -> ItemResponse:
    """An active item with its current price and available portions."""
    return ItemResponse.from_item(CatalogService(session).get(item_id))


@router.post("", status_code=status.HTTP_201_CREATED, responses=ADMIN_ERRORS)
def create_menu_item(
    draft: ItemCreate, session: DbSession, _admin: AdminUser
) -> ItemResponse:
    return ItemResponse.from_item(CatalogService(session).create(draft))


@router.put("/{item_id}", responses={**ADMIN_ERRORS, **NOT_FOUND})
def update_menu_item(
    item_id: ItemId, changes: ItemUpdate, session: DbSession, _admin: AdminUser
) -> ItemResponse:
    """Change the given fields; `"active": true` re-enables a disabled item."""
    return ItemResponse.from_item(CatalogService(session).update(item_id, changes))


@router.delete(
    "/{item_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={**ADMIN_ERRORS, **NOT_FOUND},
)
def disable_menu_item(item_id: ItemId, session: DbSession, _admin: AdminUser) -> None:
    """Soft delete: past orders keep the item, nobody can order it."""
    CatalogService(session).disable(item_id)
