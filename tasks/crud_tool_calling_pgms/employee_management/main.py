"""Employee Management: FastAPI CRUD over MySQL, plus an OpenAI chatbot that
performs the same CRUD operations through tool calling.

Run:  uv run fastapi dev main.py   (then open http://127.0.0.1:8000)
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from agents import ChatMessage, answer_chat
from employees import (
    DuplicateEmailError,
    Employee,
    EmployeeCreate,
    EmployeeNotFoundError,
    EmployeeUpdate,
    create_employee,
    delete_employee,
    get_employee,
    init_db,
    list_employees,
    update_employee,
)

APP_DIR = Path(__file__).resolve().parent
load_dotenv(APP_DIR / ".env")


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1)


class ChatReply(BaseModel):
    reply: str


# ---------------------------------------------------------------- HTTP API


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    init_db()
    yield


app = FastAPI(title="Employee Management", lifespan=lifespan)


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(APP_DIR / "index.html")


@app.get("/api/employees")
def read_employees(search: str | None = None) -> list[Employee]:
    return list_employees(search)


@app.get("/api/employees/{employee_id}")
def read_employee(employee_id: int) -> Employee:
    try:
        return get_employee(employee_id)
    except EmployeeNotFoundError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from None


@app.post("/api/employees", status_code=status.HTTP_201_CREATED)
def add_employee(data: EmployeeCreate) -> Employee:
    try:
        return create_employee(data)
    except DuplicateEmailError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from None


@app.patch("/api/employees/{employee_id}")
def edit_employee(employee_id: int, changes: EmployeeUpdate) -> Employee:
    try:
        return update_employee(employee_id, changes)
    except EmployeeNotFoundError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from None
    except DuplicateEmailError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from None


@app.delete("/api/employees/{employee_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_employee(employee_id: int) -> None:
    try:
        delete_employee(employee_id)
    except EmployeeNotFoundError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from None


@app.post("/api/chat")
def chat(request: ChatRequest) -> ChatReply:
    return ChatReply(reply=answer_chat(request.messages))
