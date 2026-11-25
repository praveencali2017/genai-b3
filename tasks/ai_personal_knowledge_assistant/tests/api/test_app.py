"""App-level behaviour: import side effects, lifespan, health, error mapping, docs."""

import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ai_personal_knowledge_assistant.core.errors import (
    AgentStepLimitError,
    AssistantNotConfiguredError,
    AssistantUnavailableError,
    DocumentNotFoundError,
    DocumentStateError,
    KnowledgeAssistantError,
    VectorStoreError,
)
from ai_personal_knowledge_assistant.core.settings import Settings


def test_importing_the_app_opens_nothing(tmp_path: Path) -> None:
    """uvicorn imports `api.main:app`; only the lifespan may touch storage."""
    data_dir = tmp_path / "data"
    env = {
        **os.environ,
        "DATABASE_URL": f"sqlite:///{data_dir / 'app.db'}",
        "CHROMA_DIR": str(data_dir / "chroma"),
        "UPLOAD_DIR": str(data_dir / "uploads"),
    }
    script = (
        "import sys\n"
        "from ai_personal_knowledge_assistant.api.main import app\n"
        "assert 'sentence_transformers' not in sys.modules, 'model library loaded'\n"
        "assert 'torch' not in sys.modules, 'torch loaded'\n"
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 0, result.stderr
    assert not data_dir.exists()


def test_the_lifespan_creates_the_stores(app: FastAPI, settings: Settings) -> None:
    assert not settings.chroma_dir.exists()

    with TestClient(app):
        assert settings.chroma_dir.is_dir()
        assert Path(settings.database_url.removeprefix("sqlite:///")).is_file()


def test_health_reports_counts(client: TestClient, sample_docs_dir: Path) -> None:
    assert client.get("/health").json() == {
        "status": "ok",
        "ready_documents": 0,
        "stored_chunks": 0,
    }
    content = (sample_docs_dir / "Travel_Policy.txt").read_bytes()
    uploaded = client.post(
        "/documents/upload", files={"file": ("Travel_Policy.txt", content)}
    ).json()

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "ready_documents": 1,
        "stored_chunks": uploaded["chunk_count"],
    }


def test_openapi_documents_the_required_endpoints(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()

    assert schema["info"]["title"] == "AI Personal Knowledge Assistant"
    paths = schema["paths"]
    assert "post" in paths["/documents/upload"]
    assert "get" in paths["/documents"]
    assert "post" in paths["/chat/ask"]
    assert "post" in paths["/agent/chat"]
    assert "/health" in paths
    agent_responses = paths["/agent/chat"]["post"]["responses"]
    assert {"200", "422", "502", "503"} <= set(agent_responses)
    upload_responses = schema["paths"]["/documents/upload"]["post"]["responses"]
    assert {"201", "409", "413", "415", "422", "503"} <= set(upload_responses)
    assert client.get("/docs").status_code == 200


@pytest.mark.parametrize(
    ("error", "status_code"),
    [
        (VectorStoreError("search", RuntimeError("down")), 503),
        (AssistantNotConfiguredError("OPENAI_API_KEY is not set."), 503),
        (AssistantUnavailableError("OpenAI timed out."), 502),
        (AgentStepLimitError(6), 502),
        (DocumentNotFoundError(7), 404),
        (DocumentStateError(1, "ready", "failed"), 409),
        (KnowledgeAssistantError("unmapped"), 500),
    ],
)
def test_domain_errors_become_json_with_their_status(
    app: FastAPI, error: KnowledgeAssistantError, status_code: int
) -> None:
    def fail() -> None:
        raise error

    app.add_api_route("/fail", fail)
    with TestClient(app) as client:
        response = client.get("/fail")

    assert response.status_code == status_code
    assert response.json() == {"detail": str(error)}


def test_a_subclass_without_its_own_mapping_uses_its_parents(app: FastAPI) -> None:
    class SlowVectorStoreError(VectorStoreError):
        pass

    def fail() -> None:
        raise SlowVectorStoreError("search", RuntimeError("slow"))

    app.add_api_route("/fail", fail)
    with TestClient(app) as client:
        assert client.get("/fail").status_code == 503


def test_a_missing_key_is_logged_as_a_warning_without_a_traceback(
    app: FastAPI, caplog: pytest.LogCaptureFixture
) -> None:
    def fail() -> None:
        raise AssistantNotConfiguredError("OPENAI_API_KEY is not set.")

    app.add_api_route("/fail", fail)
    with TestClient(app) as client, caplog.at_level(logging.WARNING):
        client.get("/fail")

    (record,) = [r for r in caplog.records if r.name.endswith("api.main")]
    assert record.levelno == logging.WARNING
    assert record.exc_info is None
    assert "OPENAI_API_KEY" in record.getMessage()


def test_a_model_failure_is_logged_as_an_error_with_a_traceback(
    app: FastAPI, caplog: pytest.LogCaptureFixture
) -> None:
    def fail() -> None:
        raise AssistantUnavailableError("OpenAI timed out.")

    app.add_api_route("/fail", fail)
    with TestClient(app) as client, caplog.at_level(logging.WARNING):
        client.get("/fail")

    (record,) = [r for r in caplog.records if r.name.endswith("api.main")]
    assert record.levelno == logging.ERROR
    assert record.exc_info is not None
