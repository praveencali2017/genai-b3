"""Document metadata: the catalogue of uploaded files and their ingestion status."""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ai_personal_knowledge_assistant.core.errors import (
    DocumentNotFoundError,
    DocumentStateError,
    DuplicateDocumentError,
)
from ai_personal_knowledge_assistant.storage.models import Document
from ai_personal_knowledge_assistant.storage.records import (
    DocumentRecord,
    DocumentStatus,
    FileType,
)


class DocumentRepository:
    """Reads and writes `documents` rows inside the caller's transaction.

    Methods `flush()` so generated ids and constraint violations surface
    immediately, but never commit: the caller owns the unit of work (see
    `storage.database.session_scope`).
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(
        self, *, filename: str, file_type: FileType, size_bytes: int, sha256: str
    ) -> DocumentRecord:
        """Register a new upload with status `processing`.

        Raises `DuplicateDocumentError` if a document with the same content
        already exists. The pre-check gives the friendly error in the common
        case; the unique constraint catches a concurrent upload that slips
        between the check and the insert. The insert runs in a SAVEPOINT so
        that clash doesn't roll back the rest of the caller's transaction.
        """
        existing = self.get_by_sha256(sha256)
        if existing is not None:
            raise DuplicateDocumentError(existing.filename)

        document = Document(
            filename=filename,
            file_type=file_type,
            size_bytes=size_bytes,
            sha256=sha256,
        )
        try:
            with self._session.begin_nested():
                self._session.add(document)
        except IntegrityError as error:
            winner = self.get_by_sha256(sha256)
            if winner is None:
                raise  # some other constraint failed; not a duplicate
            raise DuplicateDocumentError(winner.filename) from error
        return _to_record(document)

    def get(self, document_id: int) -> DocumentRecord:
        return _to_record(self._load(document_id))

    def get_by_sha256(self, sha256: str) -> DocumentRecord | None:
        document = self._session.scalar(
            select(Document).where(Document.sha256 == sha256)
        )
        return None if document is None else _to_record(document)

    def list_documents(
        self, status: DocumentStatus | None = None
    ) -> list[DocumentRecord]:
        """All documents, newest first, optionally only those in `status`."""
        query = select(Document).order_by(
            Document.created_at.desc(), Document.id.desc()
        )
        if status is not None:
            query = query.where(Document.status == status)
        return [_to_record(document) for document in self._session.scalars(query)]

    def mark_ready(
        self, document_id: int, *, chunk_count: int, page_count: int | None
    ) -> DocumentRecord:
        """Finish a successful ingestion and record what it produced."""
        if chunk_count < 0:
            raise ValueError(f"chunk_count must be >= 0, got {chunk_count}")
        if page_count is not None and page_count < 0:
            raise ValueError(f"page_count must be >= 0, got {page_count}")
        document = self._load_processing(document_id, target=DocumentStatus.READY)
        document.status = DocumentStatus.READY
        document.chunk_count = chunk_count
        document.page_count = page_count
        document.error_message = None
        self._session.flush()
        return _to_record(document)

    def mark_failed(self, document_id: int, error_message: str) -> DocumentRecord:
        """Finish a failed ingestion, keeping the reason for the user to see."""
        document = self._load_processing(document_id, target=DocumentStatus.FAILED)
        document.status = DocumentStatus.FAILED
        document.error_message = error_message
        self._session.flush()
        return _to_record(document)

    def delete(self, document_id: int) -> None:
        self._session.delete(self._load(document_id))
        self._session.flush()

    def _load(self, document_id: int) -> Document:
        document = self._session.get(Document, document_id)
        if document is None:
            raise DocumentNotFoundError(document_id)
        return document

    def _load_processing(self, document_id: int, *, target: DocumentStatus) -> Document:
        """Only a document still being ingested can move to a final status."""
        document = self._load(document_id)
        if document.status is not DocumentStatus.PROCESSING:
            raise DocumentStateError(document_id, document.status.value, target.value)
        return document


def _to_record(document: Document) -> DocumentRecord:
    return DocumentRecord(
        id=document.id,
        filename=document.filename,
        file_type=document.file_type,
        size_bytes=document.size_bytes,
        sha256=document.sha256,
        page_count=document.page_count,
        chunk_count=document.chunk_count,
        status=document.status,
        error_message=document.error_message,
        created_at=document.created_at,
    )
