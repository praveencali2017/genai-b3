"""Alembic environment: migrates the database named by DATABASE_URL in `.env`.

The URL is passed to the engine directly rather than through `alembic.ini`,
whose interpolation would mangle a `%` in the password.
"""

from logging.config import fileConfig

from alembic import context

from ai_restaurant_ordering_assistant.config.database import create_db_engine
from ai_restaurant_ordering_assistant.config.settings import get_settings
from ai_restaurant_ordering_assistant.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Print the SQL instead of running it (`alembic upgrade head --sql`)."""
    context.configure(
        url=get_settings().database_url.get_secret_value(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_db_engine(get_settings().database_url.get_secret_value())
    try:
        with engine.connect() as connection:
            context.configure(connection=connection, target_metadata=target_metadata)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
