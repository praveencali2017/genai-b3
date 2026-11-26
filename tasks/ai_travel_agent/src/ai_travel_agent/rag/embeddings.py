"""Text embeddings through the configured embedding API."""

from collections.abc import Sequence
from itertools import batched

import openai

from ai_travel_agent.llm.openai_client import (
    create_embeddings,
    require_client,
)

# Inputs per API call; well under every provider's batch limit.
_BATCH_SIZE = 64


class Embedder:
    def __init__(self, client: openai.OpenAI | None, model: str) -> None:
        self._client = client
        self._model = model

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """One vector per text, in order. Raises `AssistantNotConfiguredError`
        without a configured API and `AssistantUnavailableError` on failure."""
        client = require_client(self._client, "Document search")
        vectors: list[list[float]] = []
        for batch in batched(texts, _BATCH_SIZE):
            vectors.extend(create_embeddings(client, self._model, list(batch)))
        return vectors
