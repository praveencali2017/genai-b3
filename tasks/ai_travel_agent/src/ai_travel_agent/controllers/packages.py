"""Catalog endpoints: public browsing, admin editing."""

from datetime import date
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Path, Query, status

from ai_travel_agent.controllers.dependencies import DbSession
from ai_travel_agent.controllers.responses import (
    ADMIN_ERRORS,
    NOT_FOUND,
)
from ai_travel_agent.exceptions.errors import PermissionDeniedError
from ai_travel_agent.repositories.package_repository import PackageFilters
from ai_travel_agent.schemas.package import (
    MAX_DURATION_DAYS,
    PackageCreate,
    PackageResponse,
    PackageUpdate,
)
from ai_travel_agent.security.basic_auth import (
    AdminUser,
    OptionalUser,
)
from ai_travel_agent.services.catalog_service import CatalogService

router = APIRouter(prefix="/api/packages", tags=["packages"])

PackageId = Annotated[int, Path(gt=0)]


@router.get("")
def list_packages(
    session: DbSession,
    user: OptionalUser,
    destination: Annotated[
        str | None,
        Query(max_length=80, description="City or region, e.g. Srinagar or Kashmir."),
    ] = None,
    max_price: Annotated[Decimal | None, Query(gt=0)] = None,
    duration_days: Annotated[int | None, Query(ge=1, le=MAX_DURATION_DAYS)] = None,
    start_date: Annotated[
        date | None, Query(description="Departing on or after this date.")
    ] = None,
    keyword: Annotated[str | None, Query(max_length=100)] = None,
    include_inactive: Annotated[
        bool,
        Query(description="Admins only: also list disabled and departed packages."),
    ] = False,
) -> list[PackageResponse]:
    """Active, upcoming packages, filtered; anyone may call this."""
    if include_inactive and not (user and user.is_admin):
        raise PermissionDeniedError("Only administrators can list disabled packages.")
    filters = PackageFilters(
        destination=destination.strip() if destination else None,
        max_price=max_price,
        duration_days=duration_days,
        start_date=start_date,
        keyword=keyword.strip() if keyword else None,
        include_inactive=include_inactive,
    )
    return [
        PackageResponse.from_package(package)
        for package in CatalogService(session).search(filters)
    ]


@router.get("/{package_id}", responses=NOT_FOUND)
def get_package(package_id: PackageId, session: DbSession) -> PackageResponse:
    """An active package with its current price and seats available."""
    return PackageResponse.from_package(CatalogService(session).get(package_id))


@router.post("", status_code=status.HTTP_201_CREATED, responses=ADMIN_ERRORS)
def create_package(
    draft: PackageCreate, session: DbSession, _admin: AdminUser
) -> PackageResponse:
    return PackageResponse.from_package(CatalogService(session).create(draft))


@router.put("/{package_id}", responses={**ADMIN_ERRORS, **NOT_FOUND})
def update_package(
    package_id: PackageId,
    changes: PackageUpdate,
    session: DbSession,
    _admin: AdminUser,
) -> PackageResponse:
    """Change the given fields; `"active": true` re-enables a disabled package."""
    package = CatalogService(session).update(package_id, changes)
    return PackageResponse.from_package(package)


@router.delete(
    "/{package_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={**ADMIN_ERRORS, **NOT_FOUND},
)
def disable_package(
    package_id: PackageId, session: DbSession, _admin: AdminUser
) -> None:
    """Soft delete: past bookings keep the package, nobody can book it."""
    CatalogService(session).disable(package_id)
