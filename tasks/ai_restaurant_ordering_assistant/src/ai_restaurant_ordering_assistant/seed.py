"""Fictional demo data: the menu, the demo accounts and three sample orders.

Idempotent: rows are matched by their natural key (dish title, username) and
only missing ones are added, so running the seed again never resets stock that
orders have used, nor a password someone changed. Sample orders go through
`OrderService` with fixed idempotency keys, so they obey the same stock and
price rules as real orders and a re-run finds them instead of ordering again.

The passwords are for the local demo only and are documented in the README.
"""

import logging
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ai_restaurant_ordering_assistant.models import MenuItem, User
from ai_restaurant_ordering_assistant.models.enums import Role
from ai_restaurant_ordering_assistant.repositories.user_repository import (
    UserRepository,
)
from ai_restaurant_ordering_assistant.schemas.order import OrderCreate
from ai_restaurant_ordering_assistant.security.passwords import hash_password
from ai_restaurant_ordering_assistant.services.order_service import OrderService

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SeedDish:
    title: str
    category: str
    vegetarian: bool
    price: Decimal
    portions: int
    description: str


@dataclass(frozen=True, slots=True)
class SeedAccount:
    username: str
    password: str
    role: Role


SEED_MENU: tuple[SeedDish, ...] = (
    SeedDish("Masala Dosa", "Breakfast", True, Decimal("90"), 20,
             "Crisp rice crepe with spiced potato filling, sambar and chutney."),
    SeedDish("Idli Sambar", "Breakfast", True, Decimal("60"), 30,
             "Three steamed rice cakes with lentil sambar and coconut chutney."),
    SeedDish("Medu Vada", "Breakfast", True, Decimal("50"), 25,
             "Two crunchy lentil fritters served with sambar."),
    SeedDish("Paneer Biryani", "Main course", True, Decimal("180"), 10,
             "Basmati rice layered with spiced cottage cheese and saffron."),
    SeedDish("Veg Thali", "Main course", True, Decimal("150"), 15,
             "Rice, two rotis, dal, two vegetable curries, curd and pickle."),
    SeedDish("Chole Bhature", "Main course", True, Decimal("120"), 18,
             "Spiced chickpea curry with two fluffy fried breads."),
    SeedDish("Chicken Biryani", "Main course", False, Decimal("240"), 12,
             "Hyderabadi dum biryani with tender chicken and raita."),
    SeedDish("Butter Chicken with Naan", "Main course", False, Decimal("260"), 8,
             "Chicken in a creamy tomato gravy with two butter naans."),
    SeedDish("Fish Curry Meal", "Main course", False, Decimal("280"), 6,
             "Coastal-style fish curry with steamed rice."),
    SeedDish("Gulab Jamun", "Dessert", True, Decimal("70"), 40,
             "Two warm milk dumplings in cardamom syrup."),
    SeedDish("Mango Lassi", "Beverages", True, Decimal("80"), 30,
             "Chilled yoghurt drink blended with Alphonso mango."),
    SeedDish("Filter Coffee", "Beverages", True, Decimal("40"), 50,
             "South Indian coffee brewed with chicory and frothed milk."),
)  # fmt: skip


@dataclass(frozen=True, slots=True)
class SeedOrder:
    username: str
    idempotency_key: str
    # Dish title -> quantity
    dishes: dict[str, int]


SEED_ACCOUNTS: tuple[SeedAccount, ...] = (
    SeedAccount("admin", "Admin@123", Role.ADMIN),
    SeedAccount("alice", "Alice@123", Role.USER),
    SeedAccount("bob", "Bob@123", Role.USER),
)


def seed_menu(session: Session) -> int:
    """Add the dishes that don't exist yet; returns how many were added."""
    existing = set(session.scalars(select(MenuItem.title)))
    missing = [dish for dish in SEED_MENU if dish.title not in existing]
    session.add_all(
        MenuItem(
            title=dish.title,
            description=dish.description,
            category=dish.category,
            vegetarian=dish.vegetarian,
            price=dish.price,
            capacity=dish.portions,
            active=True,
        )
        for dish in missing
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


SEED_ORDERS: tuple[SeedOrder, ...] = (
    SeedOrder("alice", "seed-order-1", {"Masala Dosa": 2, "Filter Coffee": 2}),
    SeedOrder("alice", "seed-order-2", {"Paneer Biryani": 1, "Mango Lassi": 1}),
    SeedOrder("bob", "seed-order-3", {"Veg Thali": 1, "Gulab Jamun": 2}),
)


def seed_orders(session: Session) -> int:
    """Place the sample orders that don't exist yet; returns how many were
    placed. Needs the accounts and menu to be seeded first."""
    item_ids = {
        title: item_id
        for title, item_id in session.execute(select(MenuItem.title, MenuItem.id))
    }
    users = UserRepository(session)
    orders = OrderService(session)
    placed = 0
    for sample in SEED_ORDERS:
        user = users.get_by_username(sample.username)
        if user is None:
            raise LookupError(f"seed account {sample.username!r} is missing")
        request = OrderCreate.model_validate(
            {
                "lines": [
                    {"item_id": item_ids[title], "quantity": quantity}
                    for title, quantity in sample.dishes.items()
                ]
            }
        )
        placed += orders.place(user, request, sample.idempotency_key).created
    return placed
