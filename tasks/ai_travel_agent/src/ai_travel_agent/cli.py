"""Console entry points.

- `travel-api`: serve the API and web UI with uvicorn.
- `travel-seed`: add the fictional tour packages, demo accounts and sample
  bookings
  (idempotent).
- `travel-index`: index the policy documents in KNOWLEDGE_BASE_DIR
  (idempotent: unchanged files are skipped, changed ones get a new version).

The schema itself comes from Alembic: `alembic upgrade head` before seeding.
"""

import argparse
import logging
import os
from collections.abc import Sequence
from pathlib import Path

from ai_travel_agent.config.database import (
    create_db_engine,
    create_session_factory,
    session_scope,
)
from ai_travel_agent.config.logging import configure_logging
from ai_travel_agent.config.settings import get_settings

API_APP = "ai_travel_agent.main:app"

logger = logging.getLogger(__name__)


def run_api(argv: Sequence[str] | None = None) -> None:
    """Serve the app; host/port default to API_HOST/API_PORT."""
    settings = get_settings()
    parser = argparse.ArgumentParser(
        prog="travel-api",
        description="Run the travel booking API and web UI.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--host", default=settings.api_host, help="bind address")
    parser.add_argument("--port", type=int, default=settings.api_port, help="bind port")
    parser.add_argument(
        "--reload", action="store_true", help="restart on code changes (development)"
    )
    args = parser.parse_args(argv)

    import uvicorn  # deferred: `--help` shouldn't pay for importing the server

    # The agent calls this server at http://127.0.0.1:API_PORT unless
    # INTERNAL_API_BASE_URL is set, so a --port override must reach the app
    # (also its reload worker, which inherits the environment).
    os.environ["API_PORT"] = str(args.port)
    get_settings.cache_clear()

    configure_logging(settings.log_level)
    uvicorn.run(
        API_APP,
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level=settings.log_level.lower(),
    )


def run_seed(argv: Sequence[str] | None = None) -> None:
    """Add the demo packages, accounts and bookings that are missing."""
    argparse.ArgumentParser(
        prog="travel-seed",
        description="Add the fictional tour packages, demo accounts and sample "
        "bookings to the database. "
        "Existing rows are left untouched. Run `alembic upgrade head` first.",
    ).parse_args(argv)

    from ai_travel_agent.seed import (
        seed_accounts,
        seed_bookings,
        seed_packages,
    )

    settings = get_settings()
    configure_logging(settings.log_level)
    engine = create_db_engine(settings.database_url.get_secret_value())
    try:
        with session_scope(create_session_factory(engine)) as session:
            accounts = seed_accounts(session)
            packages = seed_packages(session)
            bookings = seed_bookings(session)
    finally:
        engine.dispose()
    logger.info(
        "Seeded %d accounts, %d packages and %d bookings.",
        accounts,
        packages,
        bookings,
    )


def run_index(argv: Sequence[str] | None = None) -> None:
    """Index every PDF, DOCX and TXT file in the knowledge-base folder."""
    settings = get_settings()
    parser = argparse.ArgumentParser(
        prog="travel-index",
        description="Index the policy documents for the chatbot. Unchanged files "
        "are skipped; a changed file becomes a new version.",
    )
    parser.add_argument(
        "folder",
        nargs="?",
        type=Path,
        default=settings.knowledge_base_dir,
        help=f"folder to index (default: {settings.knowledge_base_dir})",
    )
    args = parser.parse_args(argv)

    from ai_travel_agent.container import build_document_services
    from ai_travel_agent.exceptions.errors import (
        DuplicateDocumentError,
        TravelError,
    )
    from ai_travel_agent.models.enums import Role
    from ai_travel_agent.repositories.user_repository import (
        UserRepository,
    )
    from ai_travel_agent.services.document_service import (
        DocumentService,
    )

    configure_logging(settings.log_level)
    files = sorted(
        path
        for path in args.folder.iterdir()
        if path.is_file() and path.suffix.lower() in {".pdf", ".docx", ".txt"}
    )
    if not files:
        raise SystemExit(f"No PDF, DOCX or TXT files in {args.folder}.")

    services = build_document_services(settings)
    engine = create_db_engine(settings.database_url.get_secret_value())
    session_factory = create_session_factory(engine)
    failures = 0
    try:
        with session_scope(session_factory) as session:
            admin = UserRepository(session).first_with_role(Role.ADMIN)
        if admin is None:
            raise SystemExit("No admin account found; run travel-seed first.")
        for path in files:
            try:
                with session_scope(session_factory) as session:
                    document = DocumentService(
                        session,
                        services.vector_store,
                        services.embedder,
                        services.indexing,
                    ).upload(path.name, path.read_bytes(), admin.id)
                    logger.info(
                        "Indexed %s v%d (%d chunks).",
                        document.filename,
                        document.version,
                        document.chunk_count,
                    )
            except DuplicateDocumentError:
                logger.info("Skipped %s: already indexed.", path.name)
            except TravelError as error:
                failures += 1
                logger.error("Could not index %s: %s", path.name, error)
    finally:
        engine.dispose()
    if failures:
        raise SystemExit(f"{failures} file(s) could not be indexed.")
