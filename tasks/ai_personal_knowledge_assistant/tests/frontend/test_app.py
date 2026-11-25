"""The Streamlit script run headless by `AppTest`, with the API client faked.

`app.py` builds `KnowledgeApiClient(...)` on every run; the fixture swaps that
class (looked up from `api_client` each time the script runs) for a fake with
canned replies, so no server is needed.
"""

from pathlib import Path
from typing import Any

import pytest
from streamlit.testing.v1 import AppTest

from ai_personal_knowledge_assistant.frontend import api_client
from ai_personal_knowledge_assistant.frontend.api_client import ApiError

APP_PATH = (
    Path(__file__).resolve().parents[2]
    / "src/ai_personal_knowledge_assistant/frontend/app.py"
)
SESSION_ID = "3f2b8c1e-0000-4000-8000-000000000001"
HR_SOURCE = {
    "citation": 1,
    "document_id": 1,
    "filename": "HR_Policy.pdf",
    "page": 2,
    "chunk_index": 1,
    "score": 0.81,
    "snippet": "Employees get 12 days of casual leave per calendar year.",
}


class FakeApiClient:
    """Canned API replies; `calls` records (method, args) in order."""

    documents: list[dict[str, Any]] = []
    ask_reply: dict[str, Any] = {}
    agent_reply: dict[str, Any] = {}
    error: ApiError | None = None
    calls: list[tuple[str, tuple[Any, ...]]] = []

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url

    def health(self) -> dict[str, Any]:
        if self.error is not None:
            raise self.error
        return {"ready_documents": len(self.documents), "stored_chunks": 0}

    def list_documents(self) -> list[dict[str, Any]]:
        if self.error is not None:
            raise self.error
        return self.documents

    def ask(self, question: str, session_id: str | None) -> dict[str, Any]:
        self.calls.append(("ask", (question, session_id)))
        return self.ask_reply

    def agent_chat(self, message: str, session_id: str | None) -> dict[str, Any]:
        self.calls.append(("agent_chat", (message, session_id)))
        return self.agent_reply


@pytest.fixture
def fake_api(monkeypatch: pytest.MonkeyPatch) -> type[FakeApiClient]:
    class Api(FakeApiClient):
        documents = []
        calls = []

    monkeypatch.setattr(api_client, "KnowledgeApiClient", Api)
    return Api


def run_app() -> AppTest:
    app = AppTest.from_file(str(APP_PATH), default_timeout=10).run()
    assert not app.exception, app.exception
    return app


def captions(app: AppTest) -> list[str]:
    return [caption.value for caption in app.caption]


def markdown(app: AppTest) -> list[str]:
    return [block.value for block in app.markdown]


def test_with_no_documents_the_ui_shows_empty_states(
    fake_api: type[FakeApiClient],
) -> None:
    app = run_app()

    assert "No documents yet — upload one above." in [info.value for info in app.info]
    assert captions(app).count("No messages yet.") == 2
    assert app.success[0].value.startswith("API online · 0 documents")


def test_an_unreachable_api_is_reported_not_raised(
    fake_api: type[FakeApiClient],
) -> None:
    fake_api.error = ApiError("Can't reach the API at http://x; start it.")

    app = run_app()

    errors = [error.value for error in app.error]
    assert "API unavailable: Can't reach the API at http://x; start it." in errors
    assert "Couldn't load documents: Can't reach the API at http://x; start it." in (
        errors
    )


def test_an_ask_renders_the_answer_and_its_sources(
    fake_api: type[FakeApiClient],
) -> None:
    fake_api.ask_reply = {
        "answer": "Employees get 12 days of casual leave per calendar year [1].",
        "is_grounded": True,
        "sources": [HR_SOURCE],
        "session_id": SESSION_ID,
    }
    app = run_app()

    app.chat_input(key="ask_chat_input").set_value("How many casual leaves?").run()

    assert not app.exception, app.exception
    assert fake_api.calls == [("ask", ("How many casual leaves?", None))]
    assert "How many casual leaves?" in markdown(app)
    assert "Employees get 12 days of casual leave per calendar year [1]." in (
        markdown(app)
    )
    assert "**[1] HR_Policy.pdf — page 2** (score 0.81)" in markdown(app)
    assert app.expander[0].label == "Sources (1)"
    assert "Not found in your documents" not in captions(app)
    assert app.session_state["ask_session_id"] == SESSION_ID


def test_a_not_found_answer_is_captioned(fake_api: type[FakeApiClient]) -> None:
    fake_api.ask_reply = {
        "answer": "I couldn't find this in your uploaded documents.",
        "is_grounded": False,
        "sources": [],
        "session_id": SESSION_ID,
    }
    app = run_app()

    app.chat_input(key="ask_chat_input").set_value("Capital of France?").run()

    assert not app.exception, app.exception
    assert "Not found in your documents" in captions(app)
    assert len(app.expander) == 0


def test_an_agent_reply_shows_the_tools_used(fake_api: type[FakeApiClient]) -> None:
    fake_api.agent_reply = {
        "reply": "You have one document: HR_Policy.pdf.",
        "tool_calls": [
            {
                "tool": "list_documents",
                "arguments": {},
                "is_error": False,
                "summary": "1 document",
            }
        ],
        "sources": [],
        "session_id": SESSION_ID,
    }
    app = run_app()

    app.chat_input(key="agent_chat_input").set_value("List my documents.").run()

    assert not app.exception, app.exception
    assert fake_api.calls == [("agent_chat", ("List my documents.", None))]
    assert "You have one document: HR_Policy.pdf." in markdown(app)
    assert "**Tools used**  \n🔧 `list_documents` — 1 document" in captions(app)
    assert app.session_state["agent_session_id"] == SESSION_ID
