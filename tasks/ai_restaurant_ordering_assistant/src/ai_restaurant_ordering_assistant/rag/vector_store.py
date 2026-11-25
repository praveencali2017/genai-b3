"""Document chunks in ChromaDB: store, search and delete them by document.

Each chunk is stored under the id `"{document_id}:{chunk_index}"` with metadata
`document_id`, `filename`, `version`, `chunk_index` and, for PDFs, `page`
(Chroma metadata can't hold None, so unpaginated chunks have no `page` key).
Chroma's own embedding function is disabled: vectors always come from our
`Embedder`, so queries and documents use the same model.
"""

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import chromadb
from chromadb.config import Settings as ChromaSettings
from chromadb.errors import ChromaError

from ai_restaurant_ordering_assistant.exceptions.errors import VectorStoreError
from ai_restaurant_ordering_assistant.rag.chunker import Chunk


@dataclass(frozen=True, slots=True)
class StoredChunkSource:
    document_id: int
    filename: str
    version: int


@dataclass(frozen=True, slots=True)
class ChunkHit:
    """A search result; `score` is cosine similarity clamped to 0..1."""

    document_id: int
    filename: str
    version: int
    page: int | None
    chunk_index: int
    text: str
    score: float

    @property
    def chunk_id(self) -> str:
        return f"{self.document_id}:{self.chunk_index}"


class ChromaVectorStore:
    def __init__(self, path: Path, collection_name: str) -> None:
        try:
            path.mkdir(parents=True, exist_ok=True)
            client = chromadb.PersistentClient(
                path=path, settings=ChromaSettings(anonymized_telemetry=False)
            )
            self._collection = client.get_or_create_collection(
                name=collection_name,
                configuration={"hnsw": {"space": "cosine"}},
                embedding_function=None,
            )
        except (OSError, ChromaError, ValueError) as error:
            raise VectorStoreError(f"open its data folder {path}", error) from error

    def add(
        self,
        source: StoredChunkSource,
        chunks: Sequence[Chunk],
        vectors: Sequence[Sequence[float]],
    ) -> None:
        """Upsert a document's chunks (a retry overwrites, never duplicates)."""
        with _chroma_errors(f"store the chunks of {source.filename}"):
            self._collection.upsert(
                ids=[f"{source.document_id}:{chunk.index}" for chunk in chunks],
                embeddings=[list(vector) for vector in vectors],
                documents=[chunk.text for chunk in chunks],
                metadatas=[_metadata(source, chunk) for chunk in chunks],
            )

    def query(
        self, vector: Sequence[float], top_k: int, document_ids: Sequence[int]
    ) -> list[ChunkHit]:
        """The `top_k` chunks closest to `vector` among `document_ids`."""
        if not document_ids:
            return []
        with _chroma_errors("search the documents"):
            result = self._collection.query(
                query_embeddings=[list(vector)],
                n_results=top_k,
                where={"document_id": {"$in": list(document_ids)}},
                include=["documents", "metadatas", "distances"],
            )
        rows = zip(
            _first(result["documents"]),
            _first(result["metadatas"]),
            _first(result["distances"]),
            strict=True,
        )
        hits = [_hit(text, metadata, distance) for text, metadata, distance in rows]
        return sorted(hits, key=lambda hit: hit.score, reverse=True)

    def delete_document(self, document_id: int) -> None:
        with _chroma_errors(f"delete the chunks of document {document_id}"):
            self._collection.delete(where={"document_id": document_id})


@contextmanager
def _chroma_errors(operation: str) -> Iterator[None]:
    try:
        yield
    except (ChromaError, ValueError) as error:
        raise VectorStoreError(operation, error) from error


def _metadata(source: StoredChunkSource, chunk: Chunk) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "document_id": source.document_id,
        "filename": source.filename,
        "version": source.version,
        "chunk_index": chunk.index,
    }
    if chunk.page is not None:
        metadata["page"] = chunk.page
    return metadata


def _first[T](per_query: list[list[T]] | None) -> list[T]:
    """Chroma answers a batch of queries; we always send exactly one."""
    return per_query[0] if per_query else []


def _hit(text: str | None, metadata: Mapping[str, Any] | None, distance: float):
    if text is None or metadata is None:
        raise VectorStoreError(
            "read a search result", ValueError("a chunk is missing its text")
        )
    page = metadata.get("page")
    return ChunkHit(
        document_id=int(metadata["document_id"]),
        filename=str(metadata["filename"]),
        version=int(metadata["version"]),
        page=None if page is None else int(page),
        chunk_index=int(metadata["chunk_index"]),
        text=text,
        score=min(1.0, max(0.0, 1.0 - distance)),
    )
