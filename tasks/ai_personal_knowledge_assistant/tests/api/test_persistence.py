"""A restart of the API keeps the documents and their vectors.

The first app uploads HR_Policy.pdf and shuts down completely: the lifespan
exits (disposing the database engine) and Chroma's cached clients are released,
as if the process ended. A new app on the same folders must then list the
document and answer from its stored chunks, without embedding it again.
"""

from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path
from typing import cast

import openai
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ai_personal_knowledge_assistant.api.container import AppContainer, open_container
from ai_personal_knowledge_assistant.api.main import create_app
from ai_personal_knowledge_assistant.core.settings import Settings
from tests.conftest import release_chroma
from tests.fakes import FakeEmbedder, FakeOpenAI
from tests.rag.conftest import FAKE_EMBEDDER_MIN_RELEVANCE

GROUNDED_REPLY = "Employees get 12 days of casual leave per calendar year [1]."

AppFactory = Callable[[FakeEmbedder, FakeOpenAI], FastAPI]


@pytest.fixture
def make_app(settings: Settings) -> AppFactory:
    """Builds an app on the test's folders; each call is a fresh "process"."""
    settings = settings.model_copy(
        update={"min_relevance": FAKE_EMBEDDER_MIN_RELEVANCE}
    )

    def make(embedder: FakeEmbedder, client: FakeOpenAI) -> FastAPI:
        def open_test_container() -> AbstractContextManager[AppContainer]:
            return open_container(
                settings,
                embedder=embedder,
                openai_client_factory=lambda _settings: cast(openai.OpenAI, client),
            )

        return create_app(open_test_container)

    return make


def test_documents_and_vectors_survive_a_restart(
    make_app: AppFactory, sample_docs_dir: Path
) -> None:
    content = (sample_docs_dir / "HR_Policy.pdf").read_bytes()
    with TestClient(make_app(FakeEmbedder(), FakeOpenAI())) as before:
        uploaded = before.post(
            "/documents/upload", files={"file": ("HR_Policy.pdf", content)}
        )
        assert uploaded.status_code == 201, uploaded.text
    release_chroma()

    embedder, fake_openai = FakeEmbedder(), FakeOpenAI()
    fake_openai.responses.script(GROUNDED_REPLY)
    with TestClient(make_app(embedder, fake_openai)) as after:
        documents = after.get("/documents").json()
        answer = after.post(
            "/chat/ask", json={"question": "How many casual leaves are allowed?"}
        )

    assert [(d["id"], d["filename"]) for d in documents] == [
        (uploaded.json()["id"], "HR_Policy.pdf")
    ]
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert (body["answer"], body["is_grounded"]) == (GROUNDED_REPLY, True)
    (source,) = body["sources"]
    assert (source["filename"], source["page"]) == ("HR_Policy.pdf", 2)
    assert "12 days of casual leave" in source["snippet"]
    assert embedder.document_batch_sizes == [], "nothing should be re-embedded"
