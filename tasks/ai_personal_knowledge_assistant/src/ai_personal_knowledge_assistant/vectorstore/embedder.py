"""Text embedding: the `Embedder` boundary and its Sentence Transformers implementation.

Everything that turns text into vectors goes through `Embedder`, so the vector
store never knows which model is used and tests can swap in a deterministic fake.
Implementations must return L2-normalized vectors: the Chroma collection uses
cosine distance, and normalized vectors keep scores comparable across models.
"""

from collections.abc import Sequence
from threading import Lock
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

_ENCODE_BATCH_SIZE = 32


class Embedder(Protocol):
    """Turns text into fixed-length, L2-normalized vectors."""

    @property
    def dimension(self) -> int:
        """Length of every vector this embedder returns."""
        ...

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """One vector per text, in the same order."""
        ...

    def embed_query(self, text: str) -> list[float]:
        """The vector for a search query."""
        ...


class SentenceTransformerEmbedder:
    """Embeds text with a Sentence Transformers model, loaded on first use.

    Loading is deferred so the API starts quickly and code paths that never embed
    (listing documents, tests with a fake) never pay for the model. A lock makes
    the first load safe when FastAPI's threadpool embeds from several requests at
    once; encoding itself is thread-safe in PyTorch inference mode.

    MiniLM was trained without query/document prefixes, so both methods encode
    the same way.
    """

    def __init__(self, model_name: str) -> None:
        self._model_name = model_name
        self._model: SentenceTransformer | None = None
        self._load_lock = Lock()

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dimension(self) -> int:
        dimension = self._loaded_model().get_embedding_dimension()
        if dimension is None:
            raise RuntimeError(
                f"Model {self._model_name!r} doesn't report an embedding dimension"
            )
        return dimension

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        return self._encode(list(texts))

    def embed_query(self, text: str) -> list[float]:
        return self._encode([text])[0]

    def _encode(self, texts: list[str]) -> list[list[float]]:
        vectors = self._loaded_model().encode(
            texts,
            batch_size=_ENCODE_BATCH_SIZE,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return vectors.tolist()

    def _loaded_model(self) -> "SentenceTransformer":
        if self._model is None:
            with self._load_lock:
                if self._model is None:
                    # Imported here: importing torch costs seconds at startup.
                    from sentence_transformers import SentenceTransformer

                    self._model = SentenceTransformer(self._model_name)
        return self._model
