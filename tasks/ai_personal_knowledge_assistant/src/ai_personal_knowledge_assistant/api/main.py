"""The FastAPI application: app factory, lifespan, and domain error -> HTTP mapping.

`app` is built at import time for uvicorn (`pka-api`), but building it only
declares routes: the database, Chroma and the embedding model are opened in the
lifespan, when the server starts.

Routes contain no try/except. Each domain error class is mapped to one HTTP
status here, and every error body has the same shape: `{"detail": "..."}`.
"""

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractContextManager, asynccontextmanager
from importlib.metadata import version

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from ai_personal_knowledge_assistant.api.container import AppContainer, open_container
from ai_personal_knowledge_assistant.api.routes import agent, chat, documents, health
from ai_personal_knowledge_assistant.core.errors import (
    AgentStepLimitError,
    AssistantNotConfiguredError,
    AssistantUnavailableError,
    ChatSessionNotFoundError,
    CorruptDocumentError,
    DocumentNotFoundError,
    DocumentStateError,
    DuplicateDocumentError,
    EmptyDocumentError,
    FileTooLargeError,
    KnowledgeAssistantError,
    UnsupportedFileError,
    VectorStoreError,
)
from ai_personal_knowledge_assistant.core.settings import get_settings

logger = logging.getLogger(__name__)

ContainerFactory = Callable[[], AbstractContextManager[AppContainer]]

# Starlette picks the handler of the most specific registered class, so the base
# class is only the fallback for a domain error without a mapping of its own.
ERROR_STATUS: dict[type[KnowledgeAssistantError], int] = {
    KnowledgeAssistantError: status.HTTP_500_INTERNAL_SERVER_ERROR,
    UnsupportedFileError: status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
    FileTooLargeError: status.HTTP_413_CONTENT_TOO_LARGE,
    EmptyDocumentError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    CorruptDocumentError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    DuplicateDocumentError: status.HTTP_409_CONFLICT,
    DocumentNotFoundError: status.HTTP_404_NOT_FOUND,
    DocumentStateError: status.HTTP_409_CONFLICT,
    ChatSessionNotFoundError: status.HTTP_404_NOT_FOUND,
    # Chroma is a dependency of this service; its failure is "try again later".
    VectorStoreError: status.HTTP_503_SERVICE_UNAVAILABLE,
    AssistantNotConfiguredError: status.HTTP_503_SERVICE_UNAVAILABLE,
    AssistantUnavailableError: status.HTTP_502_BAD_GATEWAY,
    AgentStepLimitError: status.HTTP_502_BAD_GATEWAY,
}

APP_DESCRIPTION = """
Chat with your own documents.

Upload PDF, DOCX or TXT files; their text is split into chunks, embedded with
`all-MiniLM-L6-v2` and stored in a persistent ChromaDB collection, while the
document metadata is kept in a relational database.

Ask questions with `POST /chat/ask`: answers come only from the retrieved
excerpts, cite them as [n], and list their sources. Or chat with the agent
at `POST /agent/chat`: it decides on its own whether to list the documents,
search them or answer from them, and reports the tools it used. Both need
`OPENAI_API_KEY`; uploading and listing work without it.
"""


def create_app(container_factory: ContainerFactory | None = None) -> FastAPI:
    """Build the app; `container_factory` opens its services at startup.

    The default opens them from the process settings (`.env` / environment).
    Tests pass a factory built on temporary folders and a fake embedder.
    """
    open_services = container_factory or _open_default_container

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        with open_services() as container:
            app.state.container = container
            yield

    app = FastAPI(
        title="AI Personal Knowledge Assistant",
        description=APP_DESCRIPTION,
        version=version("ai-personal-knowledge-assistant"),
        lifespan=lifespan,
    )
    for error_class in ERROR_STATUS:
        app.add_exception_handler(error_class, _handle_domain_error)
    app.add_exception_handler(Exception, _handle_unexpected_error)
    app.include_router(documents.router)
    app.include_router(chat.router)
    app.include_router(agent.router)
    app.include_router(health.router)
    return app


def _open_default_container() -> AbstractContextManager[AppContainer]:
    return open_container(get_settings())


async def _handle_domain_error(_request: Request, error: Exception) -> JSONResponse:
    status_code = _status_for(error)
    if isinstance(error, AssistantNotConfiguredError):
        # A configuration state, not a fault: no traceback to look at.
        logger.warning("Request needs the language model: %s", error)
    elif status_code >= status.HTTP_500_INTERNAL_SERVER_ERROR:
        logger.error("Request failed: %s", error, exc_info=error)
    return JSONResponse(status_code=status_code, content={"detail": str(error)})


async def _handle_unexpected_error(
    _request: Request, _error: Exception
) -> JSONResponse:
    # Starlette logs the traceback itself; the client gets no internals.
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Internal server error."},
    )


def _status_for(error: Exception) -> int:
    for error_class in type(error).__mro__:
        if error_class in ERROR_STATUS:
            return ERROR_STATUS[error_class]
    return status.HTTP_500_INTERNAL_SERVER_ERROR


app = create_app()
