"""Integration fixtures: a real MySQL test database and the full app.

The database comes from TEST_DATABASE_URL (the environment, or the project's
`.env`). Without it every test here is skipped. The schema is built by running
the Alembic migration, so the tests also prove the migration matches the code.
Each test starts from empty tables plus the seed accounts and packages.

The agent's HTTP client is wired to the TestClient through a forwarding
transport, so its tool calls go through the real REST endpoints, auth and all.
"""

import os
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest
from alembic import command
from alembic.config import Config
from dotenv import dotenv_values
from fastapi.testclient import TestClient
from sqlalchemy import Engine, inspect, select, text
from sqlalchemy.orm import Session, sessionmaker

from ai_travel_agent.config.database import (
    create_db_engine,
    create_session_factory,
    session_scope,
)
from ai_travel_agent.config.settings import PROJECT_ROOT, Settings, get_settings
from ai_travel_agent.container import open_container
from ai_travel_agent.main import create_app
from ai_travel_agent.models import Package
from ai_travel_agent.seed import seed_accounts, seed_packages
from tests.fakes import FakeChatClient, FakeEmbeddingClient

ADMIN = ("admin", "Admin@123")
ALICE = ("alice", "Alice@123")
BOB = ("bob", "Bob@123")

RunningApp = Callable[[], AbstractContextManager[TestClient]]


def _test_database_url() -> str | None:
    return os.environ.get("TEST_DATABASE_URL") or dotenv_values(
        PROJECT_ROOT / ".env"
    ).get("TEST_DATABASE_URL")


@pytest.fixture(scope="session")
def database_url() -> str:
    url = _test_database_url()
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set; skipping MySQL integration tests")
    return url


@pytest.fixture(scope="session")
def engine(database_url: str) -> Iterator[Engine]:
    engine = create_db_engine(database_url)
    with engine.begin() as connection:
        connection.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
        for table in inspect(connection).get_table_names():
            connection.execute(text(f"DROP TABLE `{table}`"))
        connection.execute(text("SET FOREIGN_KEY_CHECKS = 1"))
    _migrate(database_url)
    yield engine
    engine.dispose()


def _migrate(database_url: str) -> None:
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = database_url
    get_settings.cache_clear()
    try:
        command.upgrade(Config(str(PROJECT_ROOT / "alembic.ini")), "head")
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous
        get_settings.cache_clear()


@pytest.fixture
def session_factory(engine: Engine) -> sessionmaker[Session]:
    return create_session_factory(engine)


@pytest.fixture
def packages(engine: Engine, session_factory: sessionmaker[Session]) -> dict[str, int]:
    """Empty every table, seed the demo accounts and packages; returns package
    ids by title."""
    with engine.begin() as connection:
        connection.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
        for table in inspect(connection).get_table_names():
            if table != "alembic_version":
                connection.execute(text(f"TRUNCATE TABLE `{table}`"))
        connection.execute(text("SET FOREIGN_KEY_CHECKS = 1"))
    with session_scope(session_factory) as session:
        seed_accounts(session)
        seed_packages(session, date.today())
        rows = session.execute(select(Package.title, Package.id))
        return {title: package_id for title, package_id in rows}


@pytest.fixture
def settings(database_url: str, tmp_path: Path) -> Settings:
    return Settings(
        database_url=database_url,
        openai_api_key="test-key-not-used",
        openai_model="fake-model",
        chroma_path=tmp_path / "chroma",
        upload_dir=tmp_path / "uploads",
        # The fake bag-of-words embedder scores lower than a real model.
        min_relevance=0.05,
        chat_rate_limit_per_minute=1000,
    )


class _ForwardingTransport(httpx.BaseTransport):
    """Sends the agent's requests into the in-process app."""

    def __init__(self) -> None:
        self.client: TestClient | None = None

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if self.client is None:
            raise RuntimeError("the app is not running")
        # Rebuilt rather than forwarded: TestClient is built on a different
        # HTTP library (httpx2) and won't accept an httpx.Request object.
        response = self.client.request(
            request.method,
            str(request.url),
            headers=request.headers.multi_items(),
            content=request.read(),
        )
        return httpx.Response(
            response.status_code, headers=response.headers, content=response.content
        )


@pytest.fixture
def running_app(
    settings: Settings,
    packages: dict[str, int],
    fake_chat: FakeChatClient,
    fake_embeddings: FakeEmbeddingClient,
) -> RunningApp:
    """Starts the app (lifespan included); call again to simulate a restart."""

    @contextmanager
    def start() -> Iterator[TestClient]:
        transport = _ForwardingTransport()
        app = create_app(
            lambda: open_container(
                settings,
                chat_client_factory=lambda _: fake_chat,  # type: ignore[arg-type,return-value]
                embedding_client_factory=lambda _: fake_embeddings,  # type: ignore[arg-type,return-value]
                api_transport=transport,
            )
        )
        with TestClient(app) as client:
            transport.client = client
            yield client

    return start


@pytest.fixture
def client(running_app: RunningApp) -> Iterator[TestClient]:
    with running_app() as client:
        yield client


def count(session_factory: sessionmaker[Session], table: str) -> int:
    with session_scope(session_factory) as session:
        return int(
            session.execute(text(f"SELECT COUNT(*) FROM `{table}`")).scalar_one()
        )


def seats(session_factory: sessionmaker[Session], package_id: int) -> int:
    with session_scope(session_factory) as session:
        package = session.get(Package, package_id)
        assert package is not None
        return package.capacity


def book(
    client: TestClient,
    package_id: int,
    quantity: int = 1,
    *,
    auth: tuple[str, str] = ALICE,
    key: str = "test-key-1",
) -> httpx.Response:
    return client.post(
        "/api/bookings",
        json={"lines": [{"package_id": package_id, "quantity": quantity}]},
        headers={"Idempotency-Key": key},
        auth=auth,
    )


def error_body(response: httpx.Response) -> dict[str, Any]:
    body = response.json()
    assert set(body) >= {"code", "message", "request_id"}
    return body
