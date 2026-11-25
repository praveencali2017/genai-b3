"""Engine, session factory and schema creation for the relational database.

Nothing here runs at import time: the API's lifespan (Step 4) builds one engine
from `Settings.database_url` and hands sessions out through dependency
injection, and tests build their own engine against in-memory SQLite.

Unit of work: repositories only `flush()`; the caller owns the transaction and
decides when to commit, usually through `session_scope`. That lets one request
group several repository calls into a single atomic change.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from ai_personal_knowledge_assistant.storage.models import Base

MYSQL_POOL_RECYCLE_SECONDS = 3600


def create_db_engine(database_url: str | URL, *, echo: bool = False) -> Engine:
    """Build an engine for SQLite (default) or MySQL (`mysql+pymysql://...`).

    Does not connect. For a file-based SQLite database it does create the
    file's parent directory, because SQLite can create the file but not the
    folder.
    """
    url = make_url(database_url)
    if url.get_backend_name() == "sqlite":
        return _create_sqlite_engine(url, echo=echo)
    if url.get_backend_name() == "mysql":
        return _create_mysql_engine(url, echo=echo)
    return create_engine(url, echo=echo, pool_pre_ping=True)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Sessions bound to `engine`.

    `expire_on_commit=False` because repositories return detached records, and
    nothing should reload ORM state behind the caller's back after a commit.
    """
    return sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def session_scope(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    """One transaction: commit if the block succeeds, roll back if it raises."""
    with session_factory() as session, session.begin():
        yield session


def init_db(engine: Engine) -> None:
    """Create any missing tables. Idempotent; it never alters existing tables."""
    Base.metadata.create_all(engine)


def _is_in_memory_sqlite(url: URL) -> bool:
    return url.database in (None, "", ":memory:")


def _create_sqlite_engine(url: URL, *, echo: bool) -> Engine:
    connect_args: dict[str, Any] = {
        # FastAPI runs sync dependencies on a thread pool; SQLAlchemy's pool,
        # not sqlite3's same-thread check, keeps each connection to one user.
        "check_same_thread": False,
    }
    if _is_in_memory_sqlite(url):
        # Every new connection to ":memory:" is a new empty database, so all
        # sessions must share the single connection.
        engine = create_engine(
            url, echo=echo, connect_args=connect_args, poolclass=StaticPool
        )
    else:
        database = url.database or ""
        if not database.startswith("file:"):
            Path(database).parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(url, echo=echo, connect_args=connect_args)
    _configure_sqlite_connections(engine)
    return engine


def _configure_sqlite_connections(engine: Engine) -> None:
    """Turn on foreign keys and give SQLAlchemy control of transactions.

    SQLite ignores FOREIGN KEY clauses (so ON DELETE CASCADE) unless each
    connection enables them. The sqlite3 driver also issues its own BEGIN
    lazily, which breaks SAVEPOINTs (used to recover from a duplicate insert
    without losing the caller's transaction); disabling the driver's handling
    and emitting BEGIN ourselves is SQLAlchemy's documented fix.
    """

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_connection: Any, _connection_record: Any) -> None:
        dbapi_connection.isolation_level = None
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()

    @event.listens_for(engine, "begin")
    def _on_begin(connection: Any) -> None:
        connection.exec_driver_sql("BEGIN")


def _create_mysql_engine(url: URL, *, echo: bool) -> Engine:
    if "charset" not in url.query:
        # utf8mb4 is the only MySQL charset that holds all of Unicode (emoji,
        # many CJK characters) that can appear in filenames and chat content.
        url = url.update_query_dict({"charset": "utf8mb4"})
    return create_engine(
        url,
        echo=echo,
        # MySQL drops idle connections (wait_timeout); check and recycle them.
        pool_pre_ping=True,
        pool_recycle=MYSQL_POOL_RECYCLE_SECONDS,
    )
