"""Console entry points.

- `restaurant-api`: serve the API and web UI with uvicorn.
- `restaurant-seed`: add the fictional menu, demo accounts and sample orders
  (idempotent).

The schema itself comes from Alembic: `alembic upgrade head` before seeding.
"""

import argparse
import logging
from collections.abc import Sequence

from ai_restaurant_ordering_assistant.config.database import (
    create_db_engine,
    create_session_factory,
    session_scope,
)
from ai_restaurant_ordering_assistant.config.logging import configure_logging
from ai_restaurant_ordering_assistant.config.settings import get_settings

API_APP = "ai_restaurant_ordering_assistant.main:app"

logger = logging.getLogger(__name__)


def run_api(argv: Sequence[str] | None = None) -> None:
    """Serve the app; host/port default to API_HOST/API_PORT."""
    settings = get_settings()
    parser = argparse.ArgumentParser(
        prog="restaurant-api",
        description="Run the restaurant ordering API and web UI.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--host", default=settings.api_host, help="bind address")
    parser.add_argument("--port", type=int, default=settings.api_port, help="bind port")
    parser.add_argument(
        "--reload", action="store_true", help="restart on code changes (development)"
    )
    args = parser.parse_args(argv)

    import uvicorn  # deferred: `--help` shouldn't pay for importing the server

    configure_logging(settings.log_level)
    uvicorn.run(
        API_APP,
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level=settings.log_level.lower(),
    )


def run_seed(argv: Sequence[str] | None = None) -> None:
    """Add the demo menu and accounts that are missing."""
    argparse.ArgumentParser(
        prog="restaurant-seed",
        description="Add the fictional menu, demo accounts and sample orders to the "
        "database. "
        "Existing rows are left untouched. Run `alembic upgrade head` first.",
    ).parse_args(argv)

    from ai_restaurant_ordering_assistant.seed import (
        seed_accounts,
        seed_menu,
        seed_orders,
    )

    settings = get_settings()
    configure_logging(settings.log_level)
    engine = create_db_engine(settings.database_url.get_secret_value())
    try:
        with session_scope(create_session_factory(engine)) as session:
            accounts = seed_accounts(session)
            dishes = seed_menu(session)
            orders = seed_orders(session)
    finally:
        engine.dispose()
    logger.info(
        "Seeded %d accounts, %d dishes and %d orders.", accounts, dishes, orders
    )
