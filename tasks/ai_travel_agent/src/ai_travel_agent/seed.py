"""Fictional demo data: tour packages, the demo accounts and three sample
bookings.

Idempotent: rows are matched by their natural key (package title, username) and
only missing ones are added, so running the seed again never resets seats that
bookings have used, nor a password someone changed. Sample bookings go through
`BookingService` with fixed idempotency keys, so they obey the same seat, date
and price rules as real bookings and a re-run finds them instead of booking
again.

Departure dates are offsets from the day the seed runs, so a fresh database is
always bookable. The offsets cover each cancellation tier (full refund, 50%,
none) for the demo.

The passwords are for the local demo only and are documented in the README.
"""

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ai_travel_agent.models import Package, User
from ai_travel_agent.models.enums import Role
from ai_travel_agent.repositories.user_repository import (
    UserRepository,
)
from ai_travel_agent.schemas.booking import BookingCreate
from ai_travel_agent.security.passwords import hash_password
from ai_travel_agent.services.booking_service import BookingService

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SeedPackage:
    title: str
    destination: str
    duration_days: int
    price: Decimal
    seats: int
    departs_in_days: int
    description: str


@dataclass(frozen=True, slots=True)
class SeedAccount:
    username: str
    password: str
    role: Role


SEED_PACKAGES: tuple[SeedPackage, ...] = (
    SeedPackage("Kashmir Explorer", "Srinagar", 5, Decimal("24000"), 8, 30,
                "Shikara ride on Dal Lake, Gulmarg gondola and a day in Pahalgam."),
    SeedPackage("Coastal Escape", "Goa", 3, Decimal("12000"), 12, 14,
                "Beach stay in South Goa with a spice-farm visit and sunset cruise."),
    SeedPackage("Kashmir Valley Deluxe", "Srinagar", 5, Decimal("31000"), 6, 45,
                "Houseboat nights, Sonamarg glacier trip and a private guide."),
    SeedPackage("Ladakh High Passes", "Leh", 7, Decimal("38000"), 10, 60,
                "Khardung La, Nubra Valley camps and Pangong Lake."),
    SeedPackage("Kerala Backwaters", "Alleppey", 4, Decimal("18500"), 10, 21,
                "Overnight houseboat cruise, Kumarakom village walk and Ayurveda."),
    SeedPackage("Rishikesh Rafting Weekend", "Rishikesh", 2, Decimal("7500"), 15, 5,
                "White-water rafting, riverside camp and the Ganga aarti."),
    SeedPackage("Jaipur Heritage Trail", "Jaipur", 3, Decimal("14000"), 12, 1,
                "Amber Fort, City Palace and a block-printing workshop."),
    SeedPackage("Andaman Island Hopper", "Port Blair", 6, Decimal("42000"), 8, 40,
                "Havelock and Neil islands, snorkelling and Radhanagar beach."),
    SeedPackage("Darjeeling Tea Trails", "Darjeeling", 4, Decimal("16000"), 10, 25,
                "Toy-train ride, Tiger Hill sunrise and a tea-estate stay."),
    SeedPackage("Hampi Ruins Discovery", "Hampi", 3, Decimal("11000"), 14, 18,
                "Vijayanagara temples, coracle ride and boulder sunsets."),
    SeedPackage("Meghalaya Living Roots", "Shillong", 5, Decimal("22000"), 9, 35,
                "Living root bridges, Dawki river and Mawlynnong village."),
    SeedPackage("Coorg Coffee Retreat", "Madikeri", 3, Decimal("13500"), 12, 10,
                "Plantation homestay, Abbey Falls and Dubare elephant camp."),
)  # fmt: skip


@dataclass(frozen=True, slots=True)
class SeedBooking:
    username: str
    idempotency_key: str
    # Package title -> travellers
    packages: dict[str, int]


SEED_ACCOUNTS: tuple[SeedAccount, ...] = (
    SeedAccount("admin", "Admin@123", Role.ADMIN),
    SeedAccount("alice", "Alice@123", Role.USER),
    SeedAccount("bob", "Bob@123", Role.USER),
)

SEED_BOOKINGS: tuple[SeedBooking, ...] = (
    SeedBooking("alice", "seed-booking-1", {"Coastal Escape": 2}),
    SeedBooking("alice", "seed-booking-2", {"Rishikesh Rafting Weekend": 1}),
    SeedBooking("bob", "seed-booking-3", {"Kerala Backwaters": 2}),
)


def seed_packages(session: Session, today: date | None = None) -> int:
    """Add the packages that don't exist yet; returns how many were added."""
    seed_day = today or date.today()
    existing = set(session.scalars(select(Package.title)))
    missing = [package for package in SEED_PACKAGES if package.title not in existing]
    session.add_all(
        Package(
            title=package.title,
            destination=package.destination,
            description=package.description,
            duration_days=package.duration_days,
            price=package.price,
            capacity=package.seats,
            start_date=seed_day + timedelta(days=package.departs_in_days),
            active=True,
        )
        for package in missing
    )
    session.flush()
    return len(missing)


def seed_accounts(session: Session) -> int:
    """Add the demo accounts that don't exist yet; returns how many were added."""
    existing = set(session.scalars(select(User.username)))
    missing = [account for account in SEED_ACCOUNTS if account.username not in existing]
    session.add_all(
        User(
            username=account.username,
            password_hash=hash_password(account.password),
            role=account.role,
        )
        for account in missing
    )
    session.flush()
    return len(missing)


def seed_bookings(session: Session) -> int:
    """Create the sample bookings that don't exist yet; returns how many were
    created. Needs the accounts and packages to be seeded first."""
    package_ids = {
        title: package_id
        for title, package_id in session.execute(select(Package.title, Package.id))
    }
    users = UserRepository(session)
    bookings = BookingService(session)
    created = 0
    for sample in SEED_BOOKINGS:
        user = users.get_by_username(sample.username)
        if user is None:
            raise LookupError(f"seed account {sample.username!r} is missing")
        request = BookingCreate.model_validate(
            {
                "lines": [
                    {"package_id": package_ids[title], "quantity": travellers}
                    for title, travellers in sample.packages.items()
                ]
            }
        )
        created += bookings.place(user, request, sample.idempotency_key).created
    return created
