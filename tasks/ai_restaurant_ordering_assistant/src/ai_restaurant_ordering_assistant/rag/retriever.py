"""Policy evidence for a question: the most relevant chunks of ACTIVE documents.

Which documents are active is decided by MySQL (a superseded version stays in
Chroma but is never searched). Chunks below `min_relevance` are dropped, so an
empty result means "the documents don't cover this" and the assistant must say
the policy can't be verified.
"""

from sqlalchemy.orm import Session, sessionmaker

from ai_restaurant_ordering_assistant.config.database import session_scope
from ai_restaurant_ordering_assistant.rag.embeddings import Embedder
from ai_restaurant_ordering_assistant.rag.vector_store import (
    ChromaVectorStore,
    ChunkHit,
)
from ai_restaurant_ordering_assistant.repositories.document_repository import (
    DocumentRepository,
)


class PolicyRetriever:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        store: ChromaVectorStore,
        embedder: Embedder,
        top_k: int,
        min_relevance: float,
    ) -> None:
        self._session_factory = session_factory
        self._store = store
        self._embedder = embedder
        self._top_k = top_k
        self._min_relevance = min_relevance

    def retrieve(self, question: str) -> list[ChunkHit]:
        with session_scope(self._session_factory) as session:
            active_ids = DocumentRepository(session).active_ids()
        if not active_ids:
            return []
        [vector] = self._embedder.embed([question])
        hits = self._store.query(vector, self._top_k, active_ids)
        return [hit for hit in hits if hit.score >= self._min_relevance]
