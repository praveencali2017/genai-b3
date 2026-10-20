"""Student Management System as a REST API backed by MySQL.

Run from this folder (its own uv project):
    uv run fastapi dev main.py
Then open http://127.0.0.1:8000/ for the web UI or /docs for the API.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse, JSONResponse

from chat import (
    ChatNotConfiguredError,
    ChatService,
    ChatUnavailableError,
    get_chat_service,
)
from repository import StudentRepository, connect, get_repository
from schemas import (
    ChatReply,
    ChatRequest,
    DeleteAllResult,
    Student,
    StudentCreate,
    StudentPage,
    StudentUpdate,
)

DEFAULT_PAGE_LIMIT = 10
MAX_PAGE_LIMIT = 100
INDEX_PAGE = Path(__file__).parent / "static" / "index.html"


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
    # Creating the table at startup also makes a bad .env or unreachable
    # database fail immediately rather than on the first request.
    connection = connect()
    try:
        StudentRepository(connection).create_table()
    finally:
        connection.close()
    yield


app = FastAPI(title="Student Management System", lifespan=lifespan)

Repository = Annotated[StudentRepository, Depends(get_repository)]
Chat = Annotated[ChatService, Depends(get_chat_service)]


@app.exception_handler(ChatNotConfiguredError)
def chat_not_configured(_request: Request, error: ChatNotConfiguredError) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content={"detail": str(error)}
    )


@app.exception_handler(ChatUnavailableError)
def chat_unavailable(_request: Request, error: ChatUnavailableError) -> JSONResponse:
    return JSONResponse(status_code=status.HTTP_502_BAD_GATEWAY, content={"detail": str(error)})


def _not_found(student_id: int) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Student {student_id} not found.",
    )


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    """Serve the single-page web UI; it calls the /students API on this origin."""
    return FileResponse(INDEX_PAGE)


@app.post("/students", status_code=status.HTTP_201_CREATED)
def add_student(student: StudentCreate, repository: Repository) -> Student:
    return repository.add(student)


@app.get("/students")
def list_students(
    repository: Repository,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_LIMIT)] = DEFAULT_PAGE_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> StudentPage:
    return StudentPage(
        items=repository.list_page(limit=limit, offset=offset),
        total=repository.count(),
        limit=limit,
        offset=offset,
    )


@app.get("/students/{id}")
def get_student(id: int, repository: Repository) -> Student:
    student = repository.get(id)
    if student is None:
        raise _not_found(id)
    return student


@app.patch("/students/{id}")
def update_student(
    id: int,
    changes: StudentUpdate,
    repository: Repository,
) -> Student:
    student = repository.update(id, changes)
    if student is None:
        raise _not_found(id)
    return student


@app.delete("/students/{id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_student(id: int, repository: Repository) -> Response:
    if not repository.delete(id):
        raise _not_found(id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.delete("/students")
def delete_all_students(
    repository: Repository,
    confirm: Annotated[bool, Query()] = False,
) -> DeleteAllResult:
    # Stands in for the CLI's "are you sure?" prompt: a bare DELETE on the
    # collection must not wipe every row by accident.
    if not confirm:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Deleting all students requires ?confirm=true.",
        )
    return DeleteAllResult(deleted=repository.delete_all())


@app.post("/chat")
def chat(prompt: ChatRequest, assistant: Chat) -> ChatReply:
    """Answer one prompt from the web UI's chat widget with a short reply."""
    answer = assistant.answer(prompt.message)
    return ChatReply(reply=answer.text, model=answer.model)
