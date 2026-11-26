"""The FastAPI application: app factory, lifespan and router registration.

`app` is built at import time for uvicorn, but building it only declares routes:
the database engine is opened in the lifespan, when the server starts.
"""

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractContextManager, asynccontextmanager
from importlib.metadata import version
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from ai_travel_agent.config.settings import get_settings
from ai_travel_agent.container import AppContainer, open_container
from ai_travel_agent.controllers import (
    admin_bookings,
    auth,
    bookings,
    chat,
    documents,
    health,
    packages,
)
from ai_travel_agent.exceptions.handlers import (
    register_error_handling,
)

ContainerFactory = Callable[[], AbstractContextManager[AppContainer]]

FRONTEND_DIR = Path(__file__).resolve().parent / "frontend"

APP_DESCRIPTION = """
Browse and book fictional tour packages through this REST API, the web UI at
`/`, or the AI chatbot.

Protected endpoints use **HTTP Basic** authentication: click *Authorize* and
sign in with a demo account from the README. Every error has the body
`{"code", "message", "request_id"}`.
"""


def create_app(container_factory: ContainerFactory | None = None) -> FastAPI:
    """Build the app; `container_factory` opens its services at startup."""
    open_services = container_factory or _open_default_container

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        with open_services() as container:
            app.state.container = container
            yield

    app = FastAPI(
        title="AI Smart Travel Planner & Booking Assistant",
        description=APP_DESCRIPTION,
        version=version("ai-travel-agent"),
        lifespan=lifespan,
    )
    register_error_handling(app)
    for module in (health, auth, packages, bookings, admin_bookings, chat, documents):
        app.include_router(module.router)
    # Mounted last so the API routes above take precedence over static files.
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
    return app


def _open_default_container() -> AbstractContextManager[AppContainer]:
    return open_container(get_settings())


app = create_app()
