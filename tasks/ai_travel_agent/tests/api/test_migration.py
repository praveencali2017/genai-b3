import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine

from ai_travel_agent.models import Base

pytestmark = pytest.mark.mysql


def test_the_migration_creates_exactly_the_schema_the_models_describe(
    engine: Engine,
) -> None:
    with engine.connect() as connection:
        differences = compare_metadata(
            MigrationContext.configure(connection), Base.metadata
        )
    assert differences == []
