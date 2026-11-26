"""POST /documents/upload and GET /documents."""

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import Response

from ai_personal_knowledge_assistant.api.container import (
    AppContainer,
    build_ingestion_pipeline,
)
from ai_personal_knowledge_assistant.api.dependencies import get_ingestion_pipeline
from ai_personal_knowledge_assistant.storage.document_repository import (
    DocumentRepository,
)
from ai_personal_knowledge_assistant.storage.records import DocumentStatus
from tests.conftest import TEST_COLLECTION
from tests.fakes import FakeEmbedder, FlakyVectorStore

SAMPLE_FILES = ["HR_Policy.pdf", "Employee_Handbook.docx", "Travel_Policy.txt"]


def upload(client: TestClient, filename: str, content: bytes) -> Response:
    return client.post("/documents/upload", files={"file": (filename, content)})


def upload_sample(client: TestClient, sample_docs_dir: Path, name: str) -> Response:
    return upload(client, name, (sample_docs_dir / name).read_bytes())


def all_rows(container: AppContainer) -> list[Any]:
    with container.session_factory() as session:
        repository = DocumentRepository(session)
        return [
            row
            for status in DocumentStatus
            for row in repository.list_documents(status)
        ]


def saved_files(container: AppContainer) -> list[str]:
    upload_dir = container.settings.upload_dir
    return sorted(p.name for p in upload_dir.iterdir()) if upload_dir.exists() else []


def assert_nothing_stored(container: AppContainer) -> None:
    assert all_rows(container) == []
    assert container.vector_store.count() == 0
    assert saved_files(container) == []


# --- Upload: success ---


@pytest.mark.parametrize(
    ("filename", "file_type", "page_count"),
    [
        ("HR_Policy.pdf", "pdf", 3),
        ("Employee_Handbook.docx", "docx", None),
        ("Travel_Policy.txt", "txt", None),
    ],
)
def test_upload_indexes_a_sample_document(
    client: TestClient,
    container: AppContainer,
    sample_docs_dir: Path,
    filename: str,
    file_type: str,
    page_count: int | None,
) -> None:
    content = (sample_docs_dir / filename).read_bytes()

    response = upload(client, filename, content)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["filename"] == filename
    assert body["file_type"] == file_type
    assert body["page_count"] == page_count
    assert body["size_bytes"] == len(content)
    assert body["status"] == "ready"
    assert body["chunk_count"] > 0
    assert set(body) == {
        "id",
        "filename",
        "file_type",
        "size_bytes",
        "page_count",
        "chunk_count",
        "status",
        "created_at",
    }
    assert container.vector_store.count_for(body["id"]) == body["chunk_count"]
    stored = container.settings.upload_dir / f"{body['id']}.{file_type}"
    assert stored.read_bytes() == content


def test_a_path_traversal_name_is_stored_sanitized(
    client: TestClient, container: AppContainer, sample_docs_dir: Path, tmp_path: Path
) -> None:
    content = (sample_docs_dir / "Travel_Policy.txt").read_bytes()

    response = upload(client, "../../outside/Travel_Policy.txt", content)

    assert response.status_code == 201, response.text
    assert response.json()["filename"] == "Travel_Policy.txt"
    assert saved_files(container) == [f"{response.json()['id']}.txt"]
    assert not (tmp_path.parent / "outside").exists()


# --- Upload: rejected ---


@pytest.mark.parametrize(
    ("filename", "content"),
    [
        ("setup.exe", b"MZ\x90\x00\x03"),
        ("photo.png", b"\x89PNG\r\n\x1a\n"),
        ("fake.pdf", b"plain text pretending to be a PDF"),
    ],
)
def test_unsupported_files_are_415(
    client: TestClient, container: AppContainer, filename: str, content: bytes
) -> None:
    response = upload(client, filename, content)

    assert response.status_code == 415
    assert "detail" in response.json()
    assert_nothing_stored(container)


def test_an_oversized_file_is_413(client: TestClient, container: AppContainer) -> None:
    too_big = b"a" * (container.settings.max_upload_bytes + 1)

    response = upload(client, "big.txt", too_big)

    assert response.status_code == 413
    assert response.json() == {"detail": "File is 1.0 MB; the limit is 1 MB."}
    assert_nothing_stored(container)


def test_a_file_exactly_at_the_limit_is_accepted(
    client: TestClient, container: AppContainer
) -> None:
    words = b"policy " * (container.settings.max_upload_bytes // 7)
    at_limit = words.ljust(container.settings.max_upload_bytes, b".")

    response = upload(client, "limit.txt", at_limit)

    assert response.status_code == 201, response.text


@pytest.mark.parametrize(
    ("filename", "content"),
    [
        ("empty.txt", b""),
        ("blank.txt", b"   \n\n  "),
        ("broken.pdf", b"%PDF-1.7\nthis is not really a pdf"),
    ],
)
def test_empty_or_corrupt_files_are_422(
    client: TestClient, container: AppContainer, filename: str, content: bytes
) -> None:
    response = upload(client, filename, content)

    assert response.status_code == 422
    assert isinstance(response.json()["detail"], str)
    assert_nothing_stored(container)


def test_a_duplicate_is_409_and_stores_nothing_new(
    client: TestClient, container: AppContainer, sample_docs_dir: Path
) -> None:
    first = upload_sample(client, sample_docs_dir, "Travel_Policy.txt").json()
    content = (sample_docs_dir / "Travel_Policy.txt").read_bytes()

    response = upload(client, "renamed.txt", content)

    assert response.status_code == 409
    assert response.json() == {
        "detail": "This file was already uploaded as 'Travel_Policy.txt'."
    }
    assert [row.id for row in all_rows(container)] == [first["id"]]
    assert container.vector_store.count() == first["chunk_count"]
    assert saved_files(container) == [f"{first['id']}.txt"]


def test_a_request_without_a_file_is_422(client: TestClient) -> None:
    assert client.post("/documents/upload").status_code == 422


# --- Upload: failure mid-ingest ---


@pytest.fixture
def flaky_store(container: AppContainer, app: FastAPI) -> FlakyVectorStore:
    """Route uploads through a pipeline whose vector store can be made to fail.

    It shares the app's database and Chroma folder, so the app's own services
    see everything it writes.
    """
    store = FlakyVectorStore.persistent(
        container.settings.chroma_dir, TEST_COLLECTION, FakeEmbedder()
    )
    pipeline = build_ingestion_pipeline(
        container.settings, container.session_factory, store
    )
    app.dependency_overrides[get_ingestion_pipeline] = lambda: pipeline
    return store


def test_a_vector_store_failure_is_503_and_leaves_nothing_behind(
    client: TestClient,
    container: AppContainer,
    flaky_store: FlakyVectorStore,
    sample_docs_dir: Path,
) -> None:
    flaky_store.fail_adds = True

    response = upload_sample(client, sample_docs_dir, "HR_Policy.pdf")

    assert response.status_code == 503
    assert "could not store the chunks" in response.json()["detail"]
    assert_nothing_stored(container)


def test_the_same_file_uploads_once_the_fault_is_fixed(
    client: TestClient,
    container: AppContainer,
    flaky_store: FlakyVectorStore,
    sample_docs_dir: Path,
) -> None:
    flaky_store.fail_adds = True
    assert upload_sample(client, sample_docs_dir, "HR_Policy.pdf").status_code == 503

    flaky_store.fail_adds = False
    response = upload_sample(client, sample_docs_dir, "HR_Policy.pdf")

    assert response.status_code == 201, response.text
    body = response.json()
    assert container.vector_store.count_for(body["id"]) == body["chunk_count"]


def test_an_unexpected_failure_is_a_json_500_and_leaves_nothing_behind(
    app: FastAPI, sample_docs_dir: Path
) -> None:
    class BrokenEmbedder(FakeEmbedder):
        def embed_documents(self, texts: object) -> list[list[float]]:
            raise RuntimeError("model crashed")

    with TestClient(app, raise_server_exceptions=False) as client:
        container: AppContainer = client.app.state.container  # type: ignore[attr-defined]
        store = FlakyVectorStore.persistent(
            container.settings.chroma_dir, TEST_COLLECTION, BrokenEmbedder()
        )
        pipeline = build_ingestion_pipeline(
            container.settings, container.session_factory, store
        )
        app.dependency_overrides[get_ingestion_pipeline] = lambda: pipeline

        response = upload_sample(client, sample_docs_dir, "Travel_Policy.txt")

        assert response.status_code == 500
        assert response.json() == {"detail": "Internal server error."}
        assert_nothing_stored(container)


# --- List ---


def test_list_returns_ready_documents_newest_first(
    client: TestClient, sample_docs_dir: Path
) -> None:
    uploaded = [
        upload_sample(client, sample_docs_dir, name).json() for name in SAMPLE_FILES
    ]

    response = client.get("/documents")

    assert response.status_code == 200
    assert response.json() == list(reversed(uploaded))


def test_list_is_empty_before_any_upload(client: TestClient) -> None:
    assert client.get("/documents").json() == []


def test_list_filters_by_status(client: TestClient, sample_docs_dir: Path) -> None:
    upload_sample(client, sample_docs_dir, "Travel_Policy.txt")

    assert client.get("/documents", params={"status": "processing"}).json() == []
    assert len(client.get("/documents", params={"status": "ready"}).json()) == 1


def test_list_rejects_an_unknown_status(client: TestClient) -> None:
    assert client.get("/documents", params={"status": "deleted"}).status_code == 422
