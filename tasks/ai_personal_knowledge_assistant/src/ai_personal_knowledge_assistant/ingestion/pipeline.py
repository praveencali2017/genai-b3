"""Ingestion: one uploaded file -> a stored, searchable, `ready` document.

The steps are validate -> register the row as `processing` -> save the file ->
extract -> chunk -> embed and store the vectors -> mark the row `ready`.

Consistency across the three stores (relational DB, upload folder, Chroma) is
kept by compensation rather than a distributed transaction: once the row
exists, any failure removes that document's vectors, its saved file and finally
the row itself, then re-raises the original error. Deleting the row (instead of
marking it `failed`) means the user can fix the file and upload it again without
hitting the duplicate check, and the document list holds only usable documents.
The row goes last so that, if the process dies mid-cleanup, a leftover
`processing` row still points at whatever else was left behind.

Synchronous on purpose: extraction and embedding are CPU-bound, and the API runs
this from a sync route on FastAPI's threadpool.
"""

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from ai_personal_knowledge_assistant.ingestion.chunker import chunk_pages
from ai_personal_knowledge_assistant.ingestion.extractors import Page, extract_pages
from ai_personal_knowledge_assistant.ingestion.ocr import PageOcr
from ai_personal_knowledge_assistant.ingestion.validation import (
    ValidatedUpload,
    validate_upload,
)
from ai_personal_knowledge_assistant.storage.database import session_scope
from ai_personal_knowledge_assistant.storage.document_repository import (
    DocumentRepository,
)
from ai_personal_knowledge_assistant.storage.records import DocumentRecord, FileType
from ai_personal_knowledge_assistant.vectorstore.chroma_store import ChromaVectorStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class IngestionConfig:
    """Where uploads are kept and how they are limited and chunked."""

    upload_dir: Path
    max_bytes: int
    chunk_size: int
    chunk_overlap: int

    def __post_init__(self) -> None:
        if self.max_bytes <= 0:
            raise ValueError(f"max_bytes must be positive, got {self.max_bytes}")
        if self.chunk_size <= 0:
            raise ValueError(f"chunk_size must be positive, got {self.chunk_size}")
        if not 0 <= self.chunk_overlap < self.chunk_size:
            raise ValueError(
                f"chunk_overlap must be in [0, chunk_size), got {self.chunk_overlap}"
            )


class IngestionPipeline:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        vector_store: ChromaVectorStore,
        config: IngestionConfig,
        ocr: PageOcr | None = None,
    ) -> None:
        """`ocr` reads scanned PDF pages; None leaves pages without text out."""
        self._session_factory = session_factory
        self._vector_store = vector_store
        self._config = config
        self._ocr = ocr

    @property
    def max_bytes(self) -> int:
        return self._config.max_bytes

    def ingest(self, filename: str, content: bytes) -> DocumentRecord:
        """Store one upload and return its `ready` record.

        Raises the validation and extraction errors (`UnsupportedFileError`,
        `FileTooLargeError`, `EmptyDocumentError`, `CorruptDocumentError`,
        `OcrFailedError`, `OcrUnavailableError`), `DuplicateDocumentError`, or
        `VectorStoreError`. After any failure,
        nothing of this upload is left in the database, upload folder or
        vector store.
        """
        upload = validate_upload(filename, content, self._config.max_bytes)
        with session_scope(self._session_factory) as session:
            document = DocumentRepository(session).add(
                filename=upload.filename,
                file_type=upload.file_type,
                size_bytes=len(content),
                sha256=hashlib.sha256(content).hexdigest(),
            )
        stored_path = self._stored_path(document.id, upload.file_type)
        try:
            ready = self._process(document.id, upload, content, stored_path)
        except Exception:
            self._discard(document.id, stored_path)
            raise
        logger.info(
            "Ingested document %d (%r): %d chunks, %s pages",
            ready.id,
            ready.filename,
            ready.chunk_count,
            ready.page_count,
        )
        return ready

    def _process(
        self,
        document_id: int,
        upload: ValidatedUpload,
        content: bytes,
        stored_path: Path,
    ) -> DocumentRecord:
        stored_path.parent.mkdir(parents=True, exist_ok=True)
        stored_path.write_bytes(content)

        pages = extract_pages(content, upload.file_type, self._ocr)
        chunks = chunk_pages(pages, self._config.chunk_size, self._config.chunk_overlap)
        # SQLite hands out the id of a deleted last row again. If an earlier
        # attempt with this id left vectors behind (its cleanup failed), they
        # would otherwise be attributed to this document.
        self._vector_store.delete_document(document_id)
        chunk_count = self._vector_store.add_chunks(
            document_id, upload.filename, chunks
        )

        with session_scope(self._session_factory) as session:
            return DocumentRepository(session).mark_ready(
                document_id, chunk_count=chunk_count, page_count=_page_count(pages)
            )

    def _stored_path(self, document_id: int, file_type: FileType) -> Path:
        # Never the client's name: ids can't collide or escape the folder.
        return self._config.upload_dir / f"{document_id}.{file_type.value}"

    def _discard(self, document_id: int, stored_path: Path) -> None:
        """Best-effort cleanup of a failed ingestion.

        Each step is attempted even if an earlier one fails, and failures are
        logged rather than raised so they never hide the error that caused the
        cleanup.
        """
        try:
            self._vector_store.delete_document(document_id)
        except Exception:
            logger.exception("Could not delete the vectors of document %d", document_id)
        try:
            stored_path.unlink(missing_ok=True)
        except OSError:
            logger.exception("Could not delete the uploaded file %s", stored_path)
        try:
            with session_scope(self._session_factory) as session:
                DocumentRepository(session).delete(document_id)
        except Exception:
            logger.exception("Could not delete the row of document %d", document_id)


def _page_count(pages: list[Page]) -> int | None:
    """The last page with text for paginated formats (PDF); None otherwise.

    Extraction drops blank pages but keeps real page numbers, so blank pages
    between text pages are counted and blank pages at the end are not.
    """
    numbers = [page.number for page in pages if page.number is not None]
    return max(numbers) if numbers else None
