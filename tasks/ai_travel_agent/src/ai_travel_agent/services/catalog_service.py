"""Catalog rules: who sees which packages, and how admins change them."""

import dataclasses
from collections.abc import Callable, Sequence
from datetime import date

from sqlalchemy.orm import Session

from ai_travel_agent.exceptions.errors import ResourceNotFoundError
from ai_travel_agent.models import Package
from ai_travel_agent.repositories.package_repository import (
    PackageFilters,
    PackageRepository,
)
from ai_travel_agent.schemas.package import PackageCreate, PackageUpdate

PACKAGE = "Package"


class CatalogService:
    def __init__(
        self, session: Session, *, today: Callable[[], date] = date.today
    ) -> None:
        self._packages = PackageRepository(session)
        self._today = today

    def search(self, filters: PackageFilters) -> Sequence[Package]:
        """Matching packages. Unless inactive ones are asked for (admins),
        only active packages that haven't departed yet are listed."""
        if not filters.include_inactive:
            filters = dataclasses.replace(filters, departs_after=self._today())
        return self._packages.search(filters)

    def get(self, package_id: int, *, include_inactive: bool = False) -> Package:
        """The package; a disabled one counts as missing unless
        `include_inactive`."""
        package = self._packages.get(package_id)
        if package is None or not (package.active or include_inactive):
            raise ResourceNotFoundError(PACKAGE, package_id)
        return package

    def create(self, draft: PackageCreate) -> Package:
        return self._packages.add(Package(**draft.model_dump(), active=True))

    def update(self, package_id: int, changes: PackageUpdate) -> Package:
        package = self.get(package_id, include_inactive=True)
        for name, value in changes.model_dump(exclude_unset=True).items():
            setattr(package, name, value)
        return package

    def disable(self, package_id: int) -> None:
        """Soft delete: bookings keep referring to the package, nobody can
        book it."""
        self.get(package_id, include_inactive=True).active = False
