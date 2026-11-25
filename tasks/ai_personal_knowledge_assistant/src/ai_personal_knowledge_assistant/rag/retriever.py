"""Question -> evidence: the one place the relevance policy lives.

The retriever asks the vector store for the closest chunks and keeps only those
similar enough to count as evidence (`MIN_RELEVANCE`). Everything that answers
from documents (the `/chat/ask` path and the agent's tools) retrieves through
here, so "what counts as relevant" can't drift between them.
"""

from collections.abc import Sequence

from ai_personal_knowledge_assistant.vectorstore.chroma_store import (
    ChromaVectorStore,
    RetrievedChunk,
)


class DocumentRetriever:
    def __init__(
        self, store: ChromaVectorStore, top_k: int, min_relevance: float
    ) -> None:
        if top_k < 1:
            raise ValueError(f"top_k must be at least 1, got {top_k}")
        if not 0.0 <= min_relevance <= 1.0:
            raise ValueError(
                f"min_relevance must be between 0 and 1, got {min_relevance}"
            )
        self._store = store
        self._top_k = top_k
        self._min_relevance = min_relevance

    def search(
        self,
        question: str,
        document_ids: Sequence[int] | None = None,
        top_k: int | None = None,
    ) -> list[RetrievedChunk]:
        """The chunks relevant to `question`, best first; possibly none.

        `top_k` overrides the default number of chunks considered; hits below
        the relevance threshold are dropped, so fewer may come back.
        """
        if not question.strip():
            raise ValueError("question must not be blank")
        hits = self._store.query(
            question.strip(),
            top_k=self._top_k if top_k is None else top_k,
            document_ids=document_ids,
        )
        return [hit for hit in hits if hit.score >= self._min_relevance]
