from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from ai_travel_agent.models import Package


@dataclass(frozen=True, slots=True)
class PackageFilters:
    """Catalog search criteria; None means "don't filter on this".

    `destination` matches the destination city or the title (where regions
    such as "Kashmir" appear). `start_date` keeps packages departing on or
    after that date;
    `departs_after` keeps packages departing strictly after it (the service
    sets it to today, so departed trips leave the public catalog).
    """

    destination: str | None = None
    max_price: Decimal | None = None
    duration_days: int | None = None
    start_date: date | None = None
    keyword: str | None = None
    departs_after: date | None = None
    include_inactive: bool = False


class PackageRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def search(self, filters: PackageFilters) -> Sequence[Package]:
        statement = _apply_filters(select(Package), filters)
        return self._session.scalars(
            statement.order_by(Package.start_date, Package.title)
        ).all()

    def get(self, package_id: int) -> Package | None:
        return self._session.get(Package, package_id)

    def lock(self, package_ids: Collection[int]) -> dict[int, Package]:
        """`SELECT ... FOR UPDATE` the packages, in id order so that two
        transactions locking overlapping sets can't deadlock each other.

        Missing ids are simply absent from the result.
        """
        if not package_ids:
            return {}
        packages = self._session.scalars(
            select(Package)
            .where(Package.id.in_(package_ids))
            .order_by(Package.id)
            .with_for_update()
            # A row cached in the session from an earlier read must be
            # replaced by the locked, current version.
            .execution_options(populate_existing=True)
        ).all()
        return {package.id: package for package in packages}

    def add(self, package: Package) -> Package:
        self._session.add(package)
        self._session.flush()
        return package


def _apply_filters(
    statement: Select[tuple[Package]], filters: PackageFilters
) -> Select[tuple[Package]]:
    if not filters.include_inactive:
        statement = statement.where(Package.active.is_(True))
    if filters.departs_after is not None:
        statement = statement.where(Package.start_date > filters.departs_after)
    if filters.destination:
        # Travellers search by region ("Kashmir") as often as by city
        # ("Srinagar"); the region usually appears in the title.
        pattern = f"%{_escape_like(filters.destination)}%"
        statement = statement.where(
            Package.destination.ilike(pattern, escape="\\")
            | Package.title.ilike(pattern, escape="\\")
        )
    if filters.max_price is not None:
        statement = statement.where(Package.price <= filters.max_price)
    if filters.duration_days is not None:
        statement = statement.where(Package.duration_days == filters.duration_days)
    if filters.start_date is not None:
        statement = statement.where(Package.start_date >= filters.start_date)
    if filters.keyword:
        pattern = f"%{_escape_like(filters.keyword)}%"
        statement = statement.where(
            Package.title.ilike(pattern, escape="\\")
            | Package.description.ilike(pattern, escape="\\")
            | Package.destination.ilike(pattern, escape="\\")
        )
    return statement


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
