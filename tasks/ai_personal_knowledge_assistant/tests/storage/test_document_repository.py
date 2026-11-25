"""Tests for `DocumentRepository`."""

from datetime import UTC

import pytest
from sqlalchemy.orm import Session, sessionmaker

from ai_personal_knowledge_assistant.core.errors import (
    DocumentNotFoundError,
    DocumentStateError,
    DuplicateDocumentError,
)
from ai_personal_knowledge_assistant.storage.database import session_scope
from ai_personal_knowledge_assistant.storage.document_repository import (
    DocumentRepository,
)
from ai_personal_knowledge_assistant.storage.records import (
    DocumentRecord,
    DocumentStatus,
    FileType,
)


def add_document(
    repository: DocumentRepository, name: str = "HR_Policy.pdf", sha_char: str = "a"
) -> DocumentRecord:
    return repository.add(
        filename=name,
        file_type=FileType.PDF,
        size_bytes=1234,
        sha256=sha_char * 64,
    )


def test_add_creates_a_processing_document(session: Session) -> None:
    repository = DocumentRepository(session)

    record = add_document(repository)

    assert record.id >= 1
    assert record.filename == "HR_Policy.pdf"
    assert record.file_type is FileType.PDF
    assert record.size_bytes == 1234
    assert record.status is DocumentStatus.PROCESSING
    assert (record.chunk_count, record.page_count, record.error_message) == (
        0,
        None,
        None,
    )
    assert record.created_at.tzinfo is UTC


def test_get_returns_the_stored_document(session: Session) -> None:
    repository = DocumentRepository(session)
    added = add_document(repository)
    session.expire_all()  # force a real read instead of the identity map

    fetched = repository.get(added.id)

    assert fetched == added
    assert fetched.created_at.tzinfo is UTC


def test_get_missing_document_raises_not_found(session: Session) -> None:
    with pytest.raises(DocumentNotFoundError) as caught:
        DocumentRepository(session).get(999)

    assert caught.value.document_id == 999


def test_get_by_sha256_returns_none_when_absent(session: Session) -> None:
    repository = DocumentRepository(session)
    added = add_document(repository)

    assert repository.get_by_sha256("a" * 64) == added
    assert repository.get_by_sha256("f" * 64) is None


def test_list_documents_is_newest_first(session: Session) -> None:
    repository = DocumentRepository(session)
    first = add_document(repository, "one.pdf", "1")
    second = add_document(repository, "two.pdf", "2")
    third = add_document(repository, "three.pdf", "3")

    listed = repository.list_documents()

    assert [record.id for record in listed] == [third.id, second.id, first.id]


def test_list_documents_on_empty_database_is_empty(session: Session) -> None:
    assert DocumentRepository(session).list_documents() == []


def test_list_documents_filters_by_status(session: Session) -> None:
    repository = DocumentRepository(session)
    ready = add_document(repository, "ready.pdf", "1")
    failed = add_document(repository, "failed.pdf", "2")
    processing = add_document(repository, "processing.pdf", "3")
    repository.mark_ready(ready.id, chunk_count=3, page_count=1)
    repository.mark_failed(failed.id, "boom")

    def ids_with(status: DocumentStatus) -> list[int]:
        return [record.id for record in repository.list_documents(status)]

    assert ids_with(DocumentStatus.READY) == [ready.id]
    assert ids_with(DocumentStatus.FAILED) == [failed.id]
    assert ids_with(DocumentStatus.PROCESSING) == [processing.id]


def test_duplicate_sha256_raises_with_the_original_filename(session: Session) -> None:
    repository = DocumentRepository(session)
    add_document(repository, "original.pdf")

    with pytest.raises(DuplicateDocumentError) as caught:
        add_document(repository, "copy.pdf")

    assert caught.value.filename == "original.pdf"
    assert len(repository.list_documents()) == 1


def test_duplicate_caught_by_unique_constraint_keeps_the_transaction(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A concurrent upload that passes the pre-check still maps to the domain
    error, and work done earlier in the same transaction survives."""
    repository = DocumentRepository(session)
    earlier = add_document(repository, "earlier.pdf", "e")
    add_document(repository, "original.pdf", "a")
    real_lookup = repository.get_by_sha256
    lookups: list[str] = []

    def lookup_missing_the_first_time(sha256: str) -> DocumentRecord | None:
        lookups.append(sha256)
        return None if len(lookups) == 1 else real_lookup(sha256)

    monkeypatch.setattr(repository, "get_by_sha256", lookup_missing_the_first_time)

    with pytest.raises(DuplicateDocumentError) as caught:
        add_document(repository, "copy.pdf", "a")

    assert caught.value.filename == "original.pdf"
    assert repository.get(earlier.id).filename == "earlier.pdf"


def test_mark_ready_sets_counts(session: Session) -> None:
    repository = DocumentRepository(session)
    added = add_document(repository)

    ready = repository.mark_ready(added.id, chunk_count=12, page_count=4)

    assert ready.status is DocumentStatus.READY
    assert (ready.chunk_count, ready.page_count) == (12, 4)
    assert repository.get(added.id) == ready


def test_mark_ready_accepts_unknown_page_count(session: Session) -> None:
    repository = DocumentRepository(session)
    added = add_document(repository)

    ready = repository.mark_ready(added.id, chunk_count=2, page_count=None)

    assert ready.page_count is None


def test_mark_ready_rejects_negative_counts(session: Session) -> None:
    repository = DocumentRepository(session)
    added = add_document(repository)

    with pytest.raises(ValueError, match="chunk_count"):
        repository.mark_ready(added.id, chunk_count=-1, page_count=1)


def test_mark_failed_stores_the_error(session: Session) -> None:
    repository = DocumentRepository(session)
    added = add_document(repository)

    failed = repository.mark_failed(added.id, "PDF is encrypted")

    assert failed.status is DocumentStatus.FAILED
    assert failed.error_message == "PDF is encrypted"
    assert repository.get(added.id) == failed


def test_finished_document_cannot_change_status_again(session: Session) -> None:
    repository = DocumentRepository(session)
    added = add_document(repository)
    repository.mark_failed(added.id, "boom")

    with pytest.raises(DocumentStateError) as caught:
        repository.mark_ready(added.id, chunk_count=1, page_count=1)

    assert (caught.value.current_status, caught.value.target_status) == (
        "failed",
        "ready",
    )


def test_status_change_on_missing_document_raises_not_found(session: Session) -> None:
    repository = DocumentRepository(session)

    with pytest.raises(DocumentNotFoundError):
        repository.mark_ready(42, chunk_count=1, page_count=1)
    with pytest.raises(DocumentNotFoundError):
        repository.mark_failed(42, "boom")


def test_delete_removes_the_document(session: Session) -> None:
    repository = DocumentRepository(session)
    kept = add_document(repository, "kept.pdf", "1")
    removed = add_document(repository, "removed.pdf", "2")

    repository.delete(removed.id)

    assert [record.id for record in repository.list_documents()] == [kept.id]
    with pytest.raises(DocumentNotFoundError):
        repository.get(removed.id)


def test_delete_missing_document_raises_not_found(session: Session) -> None:
    with pytest.raises(DocumentNotFoundError):
        DocumentRepository(session).delete(7)


def test_changes_persist_only_when_the_caller_commits(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as uncommitted:
        add_document(DocumentRepository(uncommitted), "rolled_back.pdf", "1")
        uncommitted.rollback()
    with session_scope(session_factory) as committed:
        add_document(DocumentRepository(committed), "kept.pdf", "2")

    with session_factory() as reader:
        names = [r.filename for r in DocumentRepository(reader).list_documents()]

    assert names == ["kept.pdf"]
