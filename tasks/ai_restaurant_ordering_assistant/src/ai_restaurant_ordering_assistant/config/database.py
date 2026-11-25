"""Engine, session factory and transaction scope for MySQL.

Nothing here runs at import time: the API's lifespan builds one engine from
`Settings.database_url`, and the CLI commands build their own.

Unit of work: repositories only `flush()`; the caller owns the transaction and
decides when to commit, usually through `session_scope`. That lets one service
call group several repository calls (lock stock, insert order, insert lines)
into a single atomic change.
"""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

MYSQL_POOL_RECYCLE_SECONDS = 3600


def create_db_engine(database_url: str, *, echo: bool = False) -> Engine:
    """An engine for `database_url`; does not connect."""
    url = make_url(database_url)
    if "charset" not in url.query:
        # utf8mb4 is the only MySQL charset that holds all of Unicode, which
        # chat messages and document names can contain.
        url = url.update_query_dict({"charset": "utf8mb4"})
    return create_engine(
        url,
        echo=echo,
        # MySQL drops idle connections (wait_timeout); check and recycle them.
        pool_pre_ping=True,
        pool_recycle=MYSQL_POOL_RECYCLE_SECONDS,
    )


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Sessions bound to `engine`.

    `expire_on_commit=False` so objects returned by a service stay readable
    after its transaction commits, when the router serializes them.
    """
    return sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def session_scope(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    """One transaction: commit if the block succeeds, roll back if it raises."""
    with session_factory() as session, session.begin():
        yield session
