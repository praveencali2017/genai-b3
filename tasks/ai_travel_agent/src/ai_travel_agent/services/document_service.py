"""Policy document upload: validate, extract, chunk, embed, index, version.

Order of work, so a failure leaves nothing half-done:

1. Validate size, name and real file type; reject exact duplicates (SHA-256).
2. Extract, chunk and embed. These are the slow, failure-prone steps; nothing
   has been written yet if they fail.
3. In the caller's transaction: insert the new version, upsert its chunks into
   Chroma, mark earlier versions of the same filename SUPERSEDED.

If the transaction then fails, Chroma may hold chunks for a document id that
never committed; retrieval only searches ids MySQL lists as ACTIVE, so those
chunks are never seen.
"""

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ai_travel_agent.exceptions.errors import (
    BusinessConflictError,
    DuplicateDocumentError,
    FileTooLargeError,
    InvalidDocumentError,
)
from ai_travel_agent.models import Document
from ai_travel_agent.models.enums import DocumentStatus
from ai_travel_agent.rag.chunker import chunk_pages
from ai_travel_agent.rag.embeddings import Embedder
from ai_travel_agent.rag.loader import detect_file_type, extract_pages
from ai_travel_agent.rag.vector_store import (
    ChromaVectorStore,
    StoredChunkSource,
)
from ai_travel_agent.repositories.document_repository import (
    DocumentRepository,
)

_UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9._ -]+")
MAX_FILENAME_LENGTH = 200


@dataclass(frozen=True, slots=True)
class IndexingConfig:
    upload_dir: Path
    max_bytes: int
    chunk_size: int
    chunk_overlap: int


class DocumentService:
    def __init__(
        self,
        session: Session,
        store: ChromaVectorStore,
        embedder: Embedder,
        config: IndexingConfig,
    ) -> None:
        self._documents = DocumentRepository(session)
        self._store = store
        self._embedder = embedder
        self._config = config

    def list_documents(self) -> Sequence[Document]:
        return self._documents.list_all()

    def upload(self, raw_filename: str, data: bytes, uploaded_by: int) -> Document:
        filename = safe_filename(raw_filename)
        if not data:
            raise InvalidDocumentError("The file is empty.")
        if len(data) > self._config.max_bytes:
            raise FileTooLargeError(len(data), self._config.max_bytes)
        file_type = detect_file_type(filename, data)
        sha256 = hashlib.sha256(data).hexdigest()
        if (duplicate := self._documents.find_by_sha256(sha256)) is not None:
            raise DuplicateDocumentError(duplicate.filename, duplicate.version)

        chunks = chunk_pages(
            extract_pages(data, file_type),
            self._config.chunk_size,
            self._config.chunk_overlap,
        )
        vectors = self._embedder.embed([chunk.text for chunk in chunks])

        previous = self._documents.lock_active(filename)
        try:
            document = self._documents.add(
                Document(
                    filename=filename,
                    sha256=sha256,
                    version=self._documents.latest_version(filename) + 1,
                    status=DocumentStatus.ACTIVE,
                    chunk_count=len(chunks),
                    uploaded_by=uploaded_by,
                )
            )
        except IntegrityError as error:
            raise BusinessConflictError(
                f"Another upload of {filename} is in progress; try again."
            ) from error
        self._store.add(
            StoredChunkSource(document.id, document.filename, document.version),
            chunks,
            vectors,
        )
        for old in previous:
            old.status = DocumentStatus.SUPERSEDED
        self._save_original(sha256, file_type.value, data)
        return document

    def _save_original(self, sha256: str, extension: str, data: bytes) -> None:
        # Content-addressed, so a retry rewrites the same file.
        self._config.upload_dir.mkdir(parents=True, exist_ok=True)
        (self._config.upload_dir / f"{sha256}.{extension}").write_bytes(data)


def safe_filename(raw: str) -> str:
    """The base name with unusual characters replaced, e.g. for display."""
    name = _UNSAFE_FILENAME_CHARS.sub("_", Path(raw or "").name).strip(" .")
    if not name or "." not in name:
        raise InvalidDocumentError("The file needs a name with an extension.")
    return name[-MAX_FILENAME_LENGTH:]
