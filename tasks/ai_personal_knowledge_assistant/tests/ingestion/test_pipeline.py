"""The ingestion pipeline on real SQLite and on-disk Chroma, with a fake embedder."""

import logging
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy.orm import Session, sessionmaker

from ai_personal_knowledge_assistant.core.errors import (
    CorruptDocumentError,
    DuplicateDocumentError,
    UnsupportedFileError,
    VectorStoreError,
)
from ai_personal_knowledge_assistant.ingestion.chunker import Chunk
from ai_personal_knowledge_assistant.ingestion.pipeline import (
    IngestionConfig,
    IngestionPipeline,
)
from ai_personal_knowledge_assistant.storage.database import (
    create_db_engine,
    create_session_factory,
    init_db,
)
from ai_personal_knowledge_assistant.storage.document_repository import (
    DocumentRepository,
)
from ai_personal_knowledge_assistant.storage.records import (
    DocumentRecord,
    DocumentStatus,
    FileType,
)
from ai_personal_knowledge_assistant.vectorstore.chroma_store import ChromaVectorStore
from tests.conftest import TEST_COLLECTION
from tests.fakes import FakeEmbedder, FlakyVectorStore
from tests.ingestion.builders import make_pdf

CORRUPT_PDF = b"%PDF-1.7\nthis is not really a pdf"


@pytest.fixture
def session_factory() -> Iterator[sessionmaker[Session]]:
    engine = create_db_engine("sqlite://")
    init_db(engine)
    yield create_session_factory(engine)
    engine.dispose()


@pytest.fixture
def upload_dir(tmp_path: Path) -> Path:
    return tmp_path / "uploads"


@pytest.fixture
def config(upload_dir: Path) -> IngestionConfig:
    return IngestionConfig(
        upload_dir=upload_dir, max_bytes=1_000_000, chunk_size=400, chunk_overlap=50
    )


@pytest.fixture
def flaky_store(chroma_dir: Path) -> FlakyVectorStore:
    return FlakyVectorStore.persistent(chroma_dir, TEST_COLLECTION, FakeEmbedder())


@pytest.fixture
def pipeline(
    session_factory: sessionmaker[Session],
    flaky_store: FlakyVectorStore,
    config: IngestionConfig,
) -> IngestionPipeline:
    return IngestionPipeline(session_factory, flaky_store, config)


def stored_documents(session_factory: sessionmaker[Session]) -> list[DocumentRecord]:
    with session_factory() as session:
        repository = DocumentRepository(session)
        return [
            document
            for status in DocumentStatus
            for document in repository.list_documents(status)
        ]


def saved_files(upload_dir: Path) -> list[str]:
    return sorted(p.name for p in upload_dir.iterdir()) if upload_dir.exists() else []


def assert_nothing_stored(
    session_factory: sessionmaker[Session],
    store: ChromaVectorStore,
    upload_dir: Path,
) -> None:
    assert stored_documents(session_factory) == []
    assert store.count() == 0
    assert saved_files(upload_dir) == []


# --- Success ---


@pytest.mark.parametrize(
    ("filename", "file_type", "page_count"),
    [
        ("HR_Policy.pdf", FileType.PDF, 3),
        ("Employee_Handbook.docx", FileType.DOCX, None),
        ("Travel_Policy.txt", FileType.TXT, None),
    ],
)
def test_ingest_stores_a_ready_document_its_vectors_and_its_file(
    pipeline: IngestionPipeline,
    flaky_store: FlakyVectorStore,
    session_factory: sessionmaker[Session],
    upload_dir: Path,
    sample_docs_dir: Path,
    filename: str,
    file_type: FileType,
    page_count: int | None,
) -> None:
    content = (sample_docs_dir / filename).read_bytes()

    document = pipeline.ingest(filename, content)

    assert document.status is DocumentStatus.READY
    assert (document.filename, document.file_type) == (filename, file_type)
    assert document.size_bytes == len(content)
    assert document.page_count == page_count
    assert document.chunk_count > 0
    assert flaky_store.count_for(document.id) == document.chunk_count
    assert stored_documents(session_factory) == [document]
    stored_file = upload_dir / f"{document.id}.{file_type.value}"
    assert stored_file.read_bytes() == content


def test_page_count_is_the_last_page_with_text(pipeline: IngestionPipeline) -> None:
    content = make_pdf(["first page", "", "third page", ""])

    document = pipeline.ingest("gaps.pdf", content)

    assert document.page_count == 3


def test_leftover_vectors_of_a_reused_id_are_replaced(
    pipeline: IngestionPipeline, flaky_store: FlakyVectorStore
) -> None:
    stale = [Chunk(index=i, text=f"stale {i}", page=None) for i in range(50)]
    flaky_store.add_chunks(1, "old.txt", stale)

    document = pipeline.ingest("fresh.txt", b"Fresh text about travel.")

    assert document.id == 1
    assert flaky_store.count_for(1) == document.chunk_count == 1


# --- Rejected before anything is stored ---


def test_an_invalid_upload_stores_nothing(
    pipeline: IngestionPipeline,
    flaky_store: FlakyVectorStore,
    session_factory: sessionmaker[Session],
    upload_dir: Path,
) -> None:
    with pytest.raises(UnsupportedFileError):
        pipeline.ingest("setup.exe", b"MZ\x90\x00")

    assert_nothing_stored(session_factory, flaky_store, upload_dir)


def test_a_duplicate_is_rejected_and_stores_nothing_new(
    pipeline: IngestionPipeline,
    flaky_store: FlakyVectorStore,
    session_factory: sessionmaker[Session],
    upload_dir: Path,
) -> None:
    first = pipeline.ingest("notes.txt", b"Leave rules and travel rules.")

    with pytest.raises(DuplicateDocumentError, match="'notes.txt'"):
        pipeline.ingest("copy.txt", b"Leave rules and travel rules.")

    assert stored_documents(session_factory) == [first]
    assert flaky_store.count() == first.chunk_count
    assert saved_files(upload_dir) == [f"{first.id}.txt"]


# --- Failures after the row exists are fully undone ---


def test_a_vector_store_failure_mid_write_leaves_nothing_behind(
    pipeline: IngestionPipeline,
    flaky_store: FlakyVectorStore,
    session_factory: sessionmaker[Session],
    upload_dir: Path,
    sample_docs_dir: Path,
) -> None:
    flaky_store.fail_adds = True

    with pytest.raises(VectorStoreError, match="injected add fault"):
        pipeline.ingest(
            "HR_Policy.pdf", (sample_docs_dir / "HR_Policy.pdf").read_bytes()
        )

    assert_nothing_stored(session_factory, flaky_store, upload_dir)


def test_the_same_file_can_be_uploaded_again_after_a_failure(
    pipeline: IngestionPipeline,
    flaky_store: FlakyVectorStore,
    sample_docs_dir: Path,
) -> None:
    content = (sample_docs_dir / "HR_Policy.pdf").read_bytes()
    flaky_store.fail_adds = True
    with pytest.raises(VectorStoreError):
        pipeline.ingest("HR_Policy.pdf", content)

    flaky_store.fail_adds = False
    document = pipeline.ingest("HR_Policy.pdf", content)

    assert document.status is DocumentStatus.READY
    assert flaky_store.count_for(document.id) == document.chunk_count


def test_an_extraction_failure_leaves_nothing_behind(
    pipeline: IngestionPipeline,
    flaky_store: FlakyVectorStore,
    session_factory: sessionmaker[Session],
    upload_dir: Path,
) -> None:
    with pytest.raises(CorruptDocumentError):
        pipeline.ingest("broken.pdf", CORRUPT_PDF)

    assert_nothing_stored(session_factory, flaky_store, upload_dir)


def test_an_unexpected_error_is_cleaned_up_and_re_raised(
    session_factory: sessionmaker[Session],
    chroma_dir: Path,
    config: IngestionConfig,
    upload_dir: Path,
) -> None:
    class BrokenEmbedder(FakeEmbedder):
        def embed_documents(self, texts: object) -> list[list[float]]:
            raise RuntimeError("model crashed")

    store = ChromaVectorStore.persistent(chroma_dir, TEST_COLLECTION, BrokenEmbedder())
    pipeline = IngestionPipeline(session_factory, store, config)

    with pytest.raises(RuntimeError, match="model crashed"):
        pipeline.ingest("notes.txt", b"Some text to embed.")

    assert_nothing_stored(session_factory, store, upload_dir)


def test_a_failing_cleanup_step_does_not_hide_the_original_error(
    pipeline: IngestionPipeline,
    flaky_store: FlakyVectorStore,
    session_factory: sessionmaker[Session],
    upload_dir: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    flaky_store.fail_deletes = True

    with caplog.at_level(logging.ERROR), pytest.raises(CorruptDocumentError):
        pipeline.ingest("broken.pdf", CORRUPT_PDF)

    # The later cleanup steps still ran.
    assert stored_documents(session_factory) == []
    assert saved_files(upload_dir) == []
    assert "Could not delete the vectors of document 1" in caplog.text


# --- Configuration ---


@pytest.mark.parametrize(
    ("max_bytes", "chunk_size", "chunk_overlap"),
    [(0, 100, 10), (100, 0, 0), (100, 100, 100), (100, 100, -1)],
)
def test_config_rejects_impossible_values(
    tmp_path: Path, max_bytes: int, chunk_size: int, chunk_overlap: int
) -> None:
    with pytest.raises(ValueError):
        IngestionConfig(
            upload_dir=tmp_path,
            max_bytes=max_bytes,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )
