"""Document chunks in ChromaDB: store, search and delete them by document.

The store owns embedding for both writes and searches, so callers hand it text
and can never query with vectors from a different model than the one that
indexed the chunks. Chroma's own embedding function is disabled
(`embedding_function=None`): it would otherwise download an ONNX model.

Each chunk is stored under the id `"{document_id}:{chunk_index}"` with metadata
`document_id`, `filename`, `chunk_index` and, for paginated formats, `page`.
Chroma metadata values can't be None, so an unpaginated chunk (DOCX, TXT) simply
has no `page` key, and reads map the missing key back to `page=None`.
"""

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from itertools import batched
from pathlib import Path
from typing import Any, Self

import chromadb
from chromadb.api import ClientAPI
from chromadb.config import Settings as ChromaSettings
from chromadb.errors import ChromaError

from ai_personal_knowledge_assistant.core.errors import VectorStoreError
from ai_personal_knowledge_assistant.ingestion.chunker import Chunk
from ai_personal_knowledge_assistant.vectorstore.embedder import Embedder

# Chunks embedded and written per round trip; far below Chroma's batch limit,
# and small enough that one batch of MiniLM embeddings stays light on memory.
_WRITE_BATCH_SIZE = 128


@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    """A chunk returned by a search.

    `score` is the cosine similarity to the query, clamped to 0..1 (higher is
    more relevant); the clamp only folds "opposite meaning" into "unrelated".
    """

    document_id: int
    filename: str
    page: int | None
    chunk_index: int
    text: str
    score: float


class ChromaVectorStore:
    """Chunks of every document in one cosine-distance Chroma collection."""

    def __init__(
        self, client: ClientAPI, collection_name: str, embedder: Embedder
    ) -> None:
        self._embedder = embedder
        with _chroma_errors(f"open collection {collection_name!r}"):
            self._collection = client.get_or_create_collection(
                name=collection_name,
                configuration={"hnsw": {"space": "cosine"}},
                embedding_function=None,
            )

    @classmethod
    def persistent(cls, path: Path, collection_name: str, embedder: Embedder) -> Self:
        """A store kept on disk under `path`, created if it doesn't exist."""
        try:
            path.mkdir(parents=True, exist_ok=True)
            client = chromadb.PersistentClient(
                path=path, settings=ChromaSettings(anonymized_telemetry=False)
            )
        except (OSError, ChromaError) as error:
            raise VectorStoreError(f"open its data folder {path}", error) from error
        return cls(client, collection_name, embedder)

    def add_chunks(
        self, document_id: int, filename: str, chunks: Sequence[Chunk]
    ) -> int:
        """Embed and store a document's chunks; returns how many were written.

        Writes are upserts keyed by document id and chunk index, so retrying a
        failed or interrupted ingestion overwrites instead of duplicating.
        """
        for batch in batched(chunks, _WRITE_BATCH_SIZE):
            texts = [chunk.text for chunk in batch]
            embeddings = self._embedder.embed_documents(texts)
            with _chroma_errors(f"store the chunks of document {document_id}"):
                self._collection.upsert(
                    ids=[_chunk_id(document_id, chunk) for chunk in batch],
                    embeddings=embeddings,
                    documents=texts,
                    metadatas=[_metadata(document_id, filename, c) for c in batch],
                )
        return len(chunks)

    def query(
        self,
        text: str,
        top_k: int,
        document_ids: Sequence[int] | None = None,
    ) -> list[RetrievedChunk]:
        """The `top_k` chunks most similar to `text`, best first.

        `document_ids` limits the search to those documents; None searches all
        of them, and an empty sequence matches nothing.
        """
        if top_k < 1:
            raise ValueError(f"top_k must be at least 1, got {top_k}")
        if document_ids is not None and not document_ids:
            return []
        embedding = self._embedder.embed_query(text)
        where = None if document_ids is None else _in_documents(document_ids)
        with _chroma_errors("search the document chunks"):
            result = self._collection.query(
                query_embeddings=[embedding],
                n_results=top_k,
                where=where,
                include=["documents", "metadatas", "distances"],
            )
        hits = [
            _retrieved_chunk(document, metadata, distance)
            for document, metadata, distance in zip(
                _first(result["documents"]),
                _first(result["metadatas"]),
                _first(result["distances"]),
                strict=True,
            )
        ]
        return sorted(hits, key=lambda hit: hit.score, reverse=True)

    def delete_document(self, document_id: int) -> None:
        """Remove every chunk of one document; a no-op if it has none."""
        with _chroma_errors(f"delete the chunks of document {document_id}"):
            self._collection.delete(where={"document_id": document_id})

    def count(self) -> int:
        """Number of chunks across all documents."""
        with _chroma_errors("count the stored chunks"):
            return self._collection.count()

    def count_for(self, document_id: int) -> int:
        """Number of chunks stored for one document."""
        with _chroma_errors(f"count the chunks of document {document_id}"):
            result = self._collection.get(
                where={"document_id": document_id}, include=[]
            )
        return len(result["ids"])


@contextmanager
def _chroma_errors(operation: str) -> Iterator[None]:
    try:
        yield
    except ChromaError as error:
        raise VectorStoreError(operation, error) from error


def _chunk_id(document_id: int, chunk: Chunk) -> str:
    return f"{document_id}:{chunk.index}"


def _metadata(document_id: int, filename: str, chunk: Chunk) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "document_id": document_id,
        "filename": filename,
        "chunk_index": chunk.index,
    }
    if chunk.page is not None:
        metadata["page"] = chunk.page
    return metadata


def _in_documents(document_ids: Sequence[int]) -> dict[str, Any]:
    return {"document_id": {"$in": list(document_ids)}}


def _first[T](per_query: list[list[T]] | None) -> list[T]:
    """Chroma answers a batch of queries; we always send exactly one."""
    return per_query[0] if per_query else []


def _retrieved_chunk(
    document: str | None, metadata: Mapping[str, Any] | None, distance: float
) -> RetrievedChunk:
    if document is None or metadata is None:
        raise VectorStoreError(
            "read a search result",
            ValueError("a stored chunk is missing its text or metadata"),
        )
    page = metadata.get("page")
    return RetrievedChunk(
        document_id=int(metadata["document_id"]),
        filename=str(metadata["filename"]),
        page=None if page is None else int(page),
        chunk_index=int(metadata["chunk_index"]),
        text=document,
        score=min(1.0, max(0.0, 1.0 - distance)),
    )
