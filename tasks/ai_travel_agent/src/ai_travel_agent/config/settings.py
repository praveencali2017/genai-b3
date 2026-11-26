"""Application settings, read once from the environment and `.env`.

Every configurable value lives here; nothing else reads `os.environ`. Values come
from (highest priority first) environment variables, then the project's `.env`
file, then the defaults below.

Relative paths are resolved against the project root (the folder holding
`pyproject.toml`), not the current working directory, so `data/` and `.env` stay
in one place whether the app is started from the project folder, the repository
root or an IDE.
"""

from functools import cache
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, PositiveInt, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

# src/ai_travel_agent/config/settings.py -> project root. This
# holds for the editable install uv makes of this project, which is how it runs.
PROJECT_ROOT = Path(__file__).resolve().parents[3]

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        # `OPENAI_API_KEY=` in .env means "not set", not "the empty string".
        env_ignore_empty=True,
        # A .env shared with other tools may hold keys this app doesn't know.
        extra="ignore",
    )

    # --- MySQL (required) ---
    database_url: SecretStr

    # --- Language model (needed only by the chatbot) ---
    # Any OpenAI-compatible Chat Completions API: OpenAI by default; set
    # OPENAI_BASE_URL for another provider (Ollama, Groq, OpenRouter, ...).
    openai_api_key: SecretStr | None = None
    openai_base_url: str | None = None
    openai_model: str = Field(default="gpt-4o-mini", min_length=1)

    # --- Embeddings (needed by document indexing and retrieval) ---
    # Default to the language-model provider. Changing the embedding model
    # requires re-indexing the documents (delete CHROMA_PATH, run
    # travel-index), since old and new vectors aren't comparable.
    embedding_api_key: SecretStr | None = None
    embedding_base_url: str | None = None
    openai_embedding_model: str = Field(default="text-embedding-3-small", min_length=1)

    # --- Documents and vector store ---
    chroma_path: Path = Path("data/chroma")
    chroma_collection: str = Field(default="policy_documents", min_length=3)
    upload_dir: Path = Path("data/uploads")
    knowledge_base_dir: Path = Path("knowledge_base")
    max_upload_mb: PositiveInt = 10

    # --- RAG tuning ---
    chunk_size: PositiveInt = 800
    chunk_overlap: int = Field(default=100, ge=0)
    top_k: PositiveInt = 4
    # Cosine similarity (0..1) a chunk needs to count as evidence.
    min_relevance: float = Field(default=0.3, ge=0.0, le=1.0)

    # --- Agent and chat ---
    # The agent's tools call this API over HTTP, as the signed-in user. Unset
    # means http://127.0.0.1:API_PORT. Never derived from a request's Host
    # header, which a client controls.
    internal_api_base_url: str | None = None
    agent_max_iterations: PositiveInt = 6
    tool_timeout_seconds: float = Field(default=10.0, gt=0)
    pending_action_ttl_minutes: PositiveInt = 15
    chat_context_turns: PositiveInt = 12
    chat_rate_limit_per_minute: PositiveInt = 20

    # --- API server (`travel-api`) ---
    api_host: str = Field(default="127.0.0.1", min_length=1)
    api_port: int = Field(default=8000, ge=1, le=65535)

    # --- Logging ---
    log_level: LogLevel = "INFO"

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def internal_api_url(self) -> str:
        return self.internal_api_base_url or f"http://127.0.0.1:{self.api_port}"

    @property
    def embeddings_api_key(self) -> SecretStr | None:
        return self.embedding_api_key or self.openai_api_key

    @property
    def embeddings_base_url(self) -> str | None:
        return self.embedding_base_url or self.openai_base_url

    @field_validator("log_level", mode="before")
    @classmethod
    def _uppercase_log_level(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value

    @field_validator("chroma_path", "upload_dir", "knowledge_base_dir")
    @classmethod
    def _resolve_against_project_root(cls, path: Path) -> Path:
        return path if path.is_absolute() else PROJECT_ROOT / path

    @field_validator("database_url")
    @classmethod
    def _require_mysql_url(cls, url: SecretStr) -> SecretStr:
        # Row locks (SELECT ... FOR UPDATE) and the schema assume MySQL.
        try:
            backend = make_url(url.get_secret_value()).get_backend_name()
        except ArgumentError as error:
            raise ValueError("not a valid SQLAlchemy URL") from error
        if backend != "mysql":
            raise ValueError(
                "must be a MySQL URL, e.g. mysql+pymysql://user:pass@localhost/travel_ai"
            )
        return url

    @field_validator("internal_api_base_url", "openai_base_url", "embedding_base_url")
    @classmethod
    def _require_http_url(cls, url: str | None) -> str | None:
        if url is None:
            return None
        if not url.startswith(("http://", "https://")):
            raise ValueError("must start with http:// or https://")
        return url.rstrip("/")

    @model_validator(mode="after")
    def _overlap_smaller_than_chunk(self) -> Self:
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError(
                f"CHUNK_OVERLAP ({self.chunk_overlap}) must be smaller than "
                f"CHUNK_SIZE ({self.chunk_size})"
            )
        return self


@cache
def get_settings() -> Settings:
    """The process-wide settings, read on first use."""
    return Settings()  # type: ignore[call-arg]  # DATABASE_URL comes from the env
