"""Fictional demo data: the menu and the demo accounts.

Idempotent: rows are matched by their natural key (dish title, username) and
only missing ones are added, so running the seed again never resets stock that
orders have used, nor a password someone changed.

The passwords are for the local demo only and are documented in the README.
"""

import logging
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ai_restaurant_ordering_assistant.models import MenuItem, User
from ai_restaurant_ordering_assistant.models.enums import Role
from ai_restaurant_ordering_assistant.security.passwords import hash_password

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
