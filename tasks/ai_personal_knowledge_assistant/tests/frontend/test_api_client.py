"""The UI's HTTP client against a mock transport: requests sent, replies parsed,
and every failure turned into one readable `ApiError`."""

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from ai_personal_knowledge_assistant.frontend.api_client import (
    DEFAULT_TIMEOUT_SECONDS,
    SLOW_TIMEOUT_SECONDS,
    ApiError,
    KnowledgeApiClient,
)

BASE_URL = "http://api.test:8000"
SESSION_ID = "3f2b8c1e-0000-4000-8000-000000000001"

Handler = Callable[[httpx.Request], httpx.Response]


class RecordingTransport(httpx.MockTransport):
    """Answers every request with `handler` and keeps the requests it saw."""

    def __init__(self, handler: Handler) -> None:
        self.requests: list[httpx.Request] = []

        def record(request: httpx.Request) -> httpx.Response:
            request.read()
            self.requests.append(request)
            return handler(request)

        super().__init__(record)

    @property
    def only_request(self) -> httpx.Request:
        (request,) = self.requests
        return request


def reply(status: int = 200, **kwargs: Any) -> Handler:
    return lambda _request: httpx.Response(status, **kwargs)


def make_client(handler: Handler) -> tuple[KnowledgeApiClient, RecordingTransport]:
    transport = RecordingTransport(handler)
    # A trailing slash must not produce "//health".
    return KnowledgeApiClient(f"{BASE_URL}/", transport=transport), transport


def json_body(request: httpx.Request) -> Any:
    return json.loads(request.content)


def read_timeout(request: httpx.Request) -> float:
    return request.extensions["timeout"]["read"]


# --- Requests and successful replies ---


def test_health_gets_health_and_returns_the_body() -> None:
    health = {"status": "ok", "ready_documents": 1, "stored_chunks": 4}
    client, transport = make_client(reply(json=health))

    assert client.health() == health
    request = transport.only_request
    assert (request.method, str(request.url)) == ("GET", f"{BASE_URL}/health")
    assert read_timeout(request) == DEFAULT_TIMEOUT_SECONDS


def test_list_documents_gets_documents_and_returns_the_list() -> None:
    documents = [{"id": 1, "filename": "HR_Policy.pdf"}]
    client, transport = make_client(reply(json=documents))

    assert client.list_documents() == documents
    request = transport.only_request
    assert (request.method, request.url.path) == ("GET", "/documents")


def test_upload_posts_the_file_as_multipart() -> None:
    document = {"id": 7, "filename": "notes.txt", "chunk_count": 1}
    client, transport = make_client(reply(201, json=document))

    assert client.upload("notes.txt", b"casual leave: 12 days") == document
    request = transport.only_request
    assert (request.method, request.url.path) == ("POST", "/documents/upload")
    assert request.headers["content-type"].startswith("multipart/form-data")
    body = request.content
    assert b'name="file"; filename="notes.txt"' in body
    assert b"casual leave: 12 days" in body
    assert read_timeout(request) == SLOW_TIMEOUT_SECONDS


@pytest.mark.parametrize("session_id", [None, SESSION_ID], ids=["new", "existing"])
def test_ask_posts_the_question_and_session(session_id: str | None) -> None:
    answer = {"answer": "12 days [1].", "session_id": SESSION_ID, "sources": []}
    client, transport = make_client(reply(json=answer))

    assert client.ask("How many casual leaves?", session_id) == answer
    request = transport.only_request
    assert (request.method, request.url.path) == ("POST", "/chat/ask")
    assert json_body(request) == {
        "question": "How many casual leaves?",
        "session_id": session_id,
    }
    assert read_timeout(request) == SLOW_TIMEOUT_SECONDS


@pytest.mark.parametrize("session_id", [None, SESSION_ID], ids=["new", "existing"])
def test_agent_chat_posts_the_message_and_session(session_id: str | None) -> None:
    agent_reply = {"reply": "One document.", "session_id": SESSION_ID}
    client, transport = make_client(reply(json=agent_reply))

    assert client.agent_chat("List my uploaded documents.", session_id) == agent_reply
    request = transport.only_request
    assert (request.method, request.url.path) == ("POST", "/agent/chat")
    assert json_body(request) == {
        "message": "List my uploaded documents.",
        "session_id": session_id,
    }
    assert read_timeout(request) == SLOW_TIMEOUT_SECONDS


def test_the_default_timeout_is_configurable() -> None:
    transport = RecordingTransport(reply(json=[]))
    client = KnowledgeApiClient(BASE_URL, timeout=3.0, transport=transport)

    client.list_documents()

    assert read_timeout(transport.only_request) == 3.0


# --- Error replies ---


def test_a_string_detail_is_the_error_message() -> None:
    client, _ = make_client(reply(409, json={"detail": "HR_Policy.pdf is a duplicate"}))

    with pytest.raises(ApiError) as raised:
        client.upload("HR_Policy.pdf", b"%PDF-")

    assert raised.value.message == "HR_Policy.pdf is a duplicate"
    assert str(raised.value) == raised.value.message
    assert raised.value.status_code == 409


def test_validation_errors_name_their_fields() -> None:
    detail = [
        {"loc": ["body", "question"], "msg": "String should have at least 1 character"},
        {"loc": ["body", "session_id"], "msg": "Input should be a valid UUID"},
        {"loc": ["body"], "msg": "Field required"},
    ]
    client, _ = make_client(reply(422, json={"detail": detail}))

    with pytest.raises(ApiError) as raised:
        client.ask("", "not-a-uuid")

    assert raised.value.message == (
        "question: String should have at least 1 character; "
        "session_id: Input should be a valid UUID; "
        "Field required"
    )
    assert raised.value.status_code == 422


@pytest.mark.parametrize(
    "response_kwargs",
    [
        {"text": "<html>Bad Gateway</html>"},
        {"json": {"detail": ""}},
        {"json": {"error": "something"}},
        {"json": ["not", "a", "dict"]},
    ],
    ids=["not-json", "empty-detail", "no-detail", "not-an-object"],
)
def test_an_unreadable_error_body_falls_back_to_the_status(
    response_kwargs: dict[str, Any],
) -> None:
    client, _ = make_client(reply(502, **response_kwargs))

    with pytest.raises(ApiError) as raised:
        client.health()

    assert raised.value.message == "The API returned HTTP 502."
    assert raised.value.status_code == 502


# --- Transport failures ---


def raising(error: Exception) -> Handler:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise error

    return handler


def test_a_non_json_success_body_is_an_api_error() -> None:
    client, _ = make_client(reply(200, text="<html>proxy login</html>"))

    with pytest.raises(ApiError) as raised:
        client.list_documents()

    assert raised.value.message == (
        f"The API at {BASE_URL} returned a non-JSON response."
    )
    assert raised.value.status_code == 200


def test_a_refused_connection_says_how_to_start_the_api() -> None:
    client, _ = make_client(raising(httpx.ConnectError("Connection refused")))

    with pytest.raises(ApiError) as raised:
        client.list_documents()

    assert raised.value.message == (
        f"Can't reach the API at {BASE_URL}; start it with `uv run pka-api`."
    )
    assert raised.value.status_code is None
    assert isinstance(raised.value.__cause__, httpx.ConnectError)


def test_a_timeout_says_the_api_took_too_long() -> None:
    client, _ = make_client(raising(httpx.ReadTimeout("timed out")))

    with pytest.raises(ApiError) as raised:
        client.ask("How many casual leaves?", None)

    assert raised.value.message == f"The API at {BASE_URL} took too long to answer."
    assert raised.value.status_code is None


def test_any_other_transport_failure_names_the_url() -> None:
    client, _ = make_client(raising(httpx.RemoteProtocolError("peer closed")))

    with pytest.raises(ApiError) as raised:
        client.health()

    assert raised.value.message == f"Request to {BASE_URL}/health failed: peer closed"
    assert raised.value.status_code is None
