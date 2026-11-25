"""Application settings, read once from the environment and `.env`.

Every configurable value lives here; nothing else reads `os.environ`. Values come
from (highest priority first) environment variables, then the project's `.env`
file, then the defaults below.

Relative paths are resolved against the project root (the folder holding
`pyproject.toml`), not the current working directory. That keeps `data/` and
`.env` in one place whether `uv run pka-api` is started from the project folder,
the repository root, or an IDE, and stops a `.env` belonging to some other folder
from being picked up by accident.
"""

from functools import cache
from pathlib import Path
from typing import Literal, Self

from pydantic import (
    Field,
    NonNegativeInt,
    PositiveInt,
    SecretStr,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

# src/ai_personal_knowledge_assistant/core/settings.py -> project root. This holds
# for the editable install uv makes of this project, which is how it is run.
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

    # --- OpenAI ---
    openai_api_key: SecretStr | None = None
    openai_model: str = Field(default="gpt-5.4-mini", min_length=1)

    # --- Relational DB ---
    database_url: str = "sqlite:///data/app.db"

    # --- Vector store / files ---
    chroma_dir: Path = Path("data/chroma")
    chroma_collection: str = Field(default="documents", min_length=3, max_length=512)
    upload_dir: Path = Path("data/uploads")
    max_upload_mb: PositiveInt = 10

    # --- RAG tuning ---
    embedding_model: str = Field(
        default="sentence-transformers/all-MiniLM-L6-v2", min_length=1
    )
    chunk_size: PositiveInt = 800
    chunk_overlap: NonNegativeInt = 150
    top_k: PositiveInt = 5
    # Calibrated for MiniLM on the sample documents: answerable questions score
    # 0.34-0.59 against their chunk, questions the documents don't cover <= 0.14.
    min_relevance: float = Field(default=0.25, ge=0.0, le=1.0)

    # --- Agent ---
    agent_max_rounds: PositiveInt = 6
    chat_history_limit: PositiveInt = 20

    # --- API server (`pka-api`) ---
    api_host: str = Field(default="127.0.0.1", min_length=1)
    api_port: int = Field(default=8000, ge=1, le=65535)

    # --- Frontend ---
    api_base_url: str = "http://127.0.0.1:8000"

    # --- Optional OCR ---
    ocr_enabled: bool = False

    # --- Logging ---
    log_level: LogLevel = "INFO"

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def is_openai_configured(self) -> bool:
        return self.openai_api_key is not None

    @field_validator("log_level", mode="before")
    @classmethod
    def _uppercase_log_level(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value

    @field_validator("chroma_dir", "upload_dir")
    @classmethod
    def _resolve_against_project_root(cls, path: Path) -> Path:
        return path if path.is_absolute() else PROJECT_ROOT / path

    @field_validator("database_url")
    @classmethod
    def _resolve_sqlite_file_against_project_root(cls, url: str) -> str:
        try:
            parsed = make_url(url)
        except ArgumentError as error:
            raise ValueError(f"not a valid SQLAlchemy URL: {error}") from error
        database = parsed.database
        is_relative_sqlite_file = (
            parsed.get_backend_name() == "sqlite"
            and database not in (None, "", ":memory:")
            and not database.startswith("file:")
            and not Path(database).is_absolute()
        )
        if not is_relative_sqlite_file:
            return url
        absolute = parsed.set(database=str(PROJECT_ROOT / database))
        return absolute.render_as_string(hide_password=False)

    @field_validator("api_base_url")
    @classmethod
    def _require_http_url(cls, url: str) -> str:
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
    """The process-wide settings, read on first use.

    Tests that change the environment call `get_settings.cache_clear()`.
    """
    return Settings()
