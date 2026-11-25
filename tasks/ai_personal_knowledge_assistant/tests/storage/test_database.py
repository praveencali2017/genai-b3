"""Tests for engine construction and schema creation."""

from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine, inspect, text
from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateTable

from ai_personal_knowledge_assistant.storage.database import (
    create_db_engine,
    create_session_factory,
    init_db,
    session_scope,
)
from ai_personal_knowledge_assistant.storage.document_repository import (
    DocumentRepository,
)
from ai_personal_knowledge_assistant.storage.models import Base, UtcDateTime
from ai_personal_knowledge_assistant.storage.records import FileType


def test_init_db_creates_sqlite_file_and_parent_dirs(tmp_path: Path) -> None:
    db_file = tmp_path / "nested" / "data" / "app.db"
    engine = create_db_engine(f"sqlite:///{db_file}")
    try:
        init_db(engine)

        assert db_file.is_file()
        assert set(inspect(engine).get_table_names()) == {
            "documents",
            "chat_sessions",
            "chat_messages",
        }
    finally:
        engine.dispose()


def test_init_db_is_idempotent_and_keeps_data(tmp_path: Path) -> None:
    engine = create_db_engine(f"sqlite:///{tmp_path / 'app.db'}")
    try:
        init_db(engine)
        factory = create_session_factory(engine)
        with session_scope(factory) as session:
            DocumentRepository(session).add(
                filename="a.txt", file_type=FileType.TXT, size_bytes=1, sha256="a" * 64
            )

        init_db(engine)

        with factory() as session:
            assert len(DocumentRepository(session).list_documents()) == 1
    finally:
        engine.dispose()


def test_file_database_is_shared_across_engines(tmp_path: Path) -> None:
    """Data written through one engine is visible to a new one (a restart)."""
    url = f"sqlite:///{tmp_path / 'app.db'}"
    first = create_db_engine(url)
    init_db(first)
    with session_scope(create_session_factory(first)) as session:
        DocumentRepository(session).add(
            filename="a.txt", file_type=FileType.TXT, size_bytes=1, sha256="a" * 64
        )
    first.dispose()

    second = create_db_engine(url)
    try:
        with create_session_factory(second)() as session:
            (record,) = DocumentRepository(session).list_documents()
        assert record.filename == "a.txt"
    finally:
        second.dispose()


def test_sqlite_connections_enforce_foreign_keys(engine: Engine) -> None:
    with engine.connect() as connection:
        assert connection.execute(text("PRAGMA foreign_keys")).scalar() == 1


def test_session_scope_rolls_back_when_the_block_raises(engine: Engine) -> None:
    factory = create_session_factory(engine)

    with pytest.raises(RuntimeError), session_scope(factory) as session:
        DocumentRepository(session).add(
            filename="a.txt", file_type=FileType.TXT, size_bytes=1, sha256="a" * 64
        )
        raise RuntimeError("request failed")

    with factory() as session:
        assert DocumentRepository(session).list_documents() == []


def test_mysql_url_builds_an_engine_without_connecting() -> None:
    pytest.importorskip("pymysql")

    engine = create_db_engine("mysql+pymysql://user:secret@db.invalid:3306/pka")

    assert engine.dialect.name == "mysql"
    assert engine.dialect.driver == "pymysql"
    assert engine.url.query["charset"] == "utf8mb4"
    assert engine.url.database == "pka"
    engine.dispose()


def test_mysql_url_keeps_an_explicit_charset() -> None:
    pytest.importorskip("pymysql")

    engine = create_db_engine("mysql+pymysql://u:p@db.invalid/pka?charset=latin1")

    assert engine.url.query["charset"] == "latin1"
    engine.dispose()


def test_utc_datetime_rejects_naive_values(engine: Engine) -> None:
    with pytest.raises(ValueError, match="naive"):
        UtcDateTime().process_bind_param(datetime(2026, 1, 1), engine.dialect)


def test_schema_compiles_to_mysql_ddl() -> None:
    """MySQL refuses VARCHAR without a length; compiling every table catches
    that, and checks the MySQL-specific column types, without a server."""
    dialect = mysql.dialect()

    ddl = "\n".join(
        str(CreateTable(table).compile(dialect=dialect))
        for table in Base.metadata.sorted_tables
    )

    assert "VARCHAR(255)" in ddl  # filename
    assert "DATETIME(6)" in ddl  # microsecond timestamps
    assert "ON DELETE CASCADE" in ddl
