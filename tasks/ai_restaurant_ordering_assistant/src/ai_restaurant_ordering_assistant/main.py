"""The FastAPI application: app factory, lifespan and router registration.

`app` is built at import time for uvicorn, but building it only declares routes:
the database engine is opened in the lifespan, when the server starts.
"""

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractContextManager, asynccontextmanager
from importlib.metadata import version

from fastapi import FastAPI

from ai_restaurant_ordering_assistant.config.settings import get_settings
from ai_restaurant_ordering_assistant.container import AppContainer, open_container
from ai_restaurant_ordering_assistant.controllers import health

ContainerFactory = Callable[[], AbstractContextManager[AppContainer]]

APP_DESCRIPTION = """
Order food from a fictional restaurant, through the REST API, the web UI at `/`,
or the AI chatbot.
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
        title="AI Smart Food Ordering & Restaurant Assistant",
        description=APP_DESCRIPTION,
        version=version("ai-restaurant-ordering-assistant"),
        lifespan=lifespan,
    )
    app.include_router(health.router)
    return app


def _open_default_container() -> AbstractContextManager[AppContainer]:
    return open_container(get_settings())


app = create_app()
