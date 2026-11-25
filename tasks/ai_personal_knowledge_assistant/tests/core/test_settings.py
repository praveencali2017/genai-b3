"""Tests for loading and validating `Settings`."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from ai_personal_knowledge_assistant.core.settings import (
    PROJECT_ROOT,
    Settings,
    get_settings,
)


def test_defaults_load_without_any_configuration() -> None:
    settings = Settings()

    assert settings.openai_api_key is None
    assert not settings.is_openai_configured
    assert settings.openai_model == "gpt-5.4-mini"
    assert settings.embedding_model == "sentence-transformers/all-MiniLM-L6-v2"
    assert (settings.chunk_size, settings.chunk_overlap) == (800, 150)
    assert settings.top_k == 5
    assert settings.min_relevance == pytest.approx(0.30)
    assert settings.agent_max_rounds == 6
    assert settings.max_upload_bytes == 10 * 1024 * 1024
    assert settings.ocr_enabled is False
    assert settings.log_level == "INFO"


def test_env_vars_override_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("TOP_K", "8")
    monkeypatch.setenv("MIN_RELEVANCE", "0.5")
    monkeypatch.setenv("OCR_ENABLED", "true")
    monkeypatch.setenv("LOG_LEVEL", "debug")

    settings = Settings()

    assert settings.openai_api_key is not None
    assert settings.openai_api_key.get_secret_value() == "sk-test"
    assert settings.top_k == 8
    assert settings.min_relevance == pytest.approx(0.5)
    assert settings.ocr_enabled is True
    assert settings.log_level == "DEBUG"


def test_values_are_read_from_env_file(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("CHUNK_SIZE=1000\nOPENAI_MODEL=gpt-test\nUNRELATED_KEY=x\n")

    settings = Settings(_env_file=env_file)

    assert settings.chunk_size == 1000
    assert settings.openai_model == "gpt-test"


def test_env_var_wins_over_env_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("TOP_K=3\n")
    monkeypatch.setenv("TOP_K", "7")

    assert Settings(_env_file=env_file).top_k == 7


def test_blank_api_key_in_env_file_means_not_configured(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("OPENAI_API_KEY=\n")

    assert Settings(_env_file=env_file).openai_api_key is None


def test_api_key_is_not_exposed_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-very-secret")

    assert "sk-very-secret" not in repr(Settings())


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("CHUNK_OVERLAP", "800"),  # equal to the default CHUNK_SIZE
        ("CHUNK_OVERLAP", "900"),
        ("CHUNK_OVERLAP", "-1"),
        ("CHUNK_SIZE", "0"),
        ("MIN_RELEVANCE", "1.5"),
        ("MIN_RELEVANCE", "-0.1"),
        ("TOP_K", "0"),
        ("MAX_UPLOAD_MB", "-5"),
        ("AGENT_MAX_ROUNDS", "abc"),
        ("API_PORT", "70000"),
        ("LOG_LEVEL", "verbose"),
        ("API_BASE_URL", "localhost:8000"),
        ("DATABASE_URL", "not a url"),
    ],
)
def test_invalid_values_fail_fast(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv(name, value)

    with pytest.raises(ValidationError, match=name.lower()):
        Settings()


def test_overlap_error_names_both_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHUNK_SIZE", "100")
    monkeypatch.setenv("CHUNK_OVERLAP", "100")

    with pytest.raises(ValidationError, match="CHUNK_OVERLAP .* CHUNK_SIZE"):
        Settings()


def test_relative_paths_resolve_against_project_root() -> None:
    settings = Settings()

    assert settings.chroma_dir == PROJECT_ROOT / "data" / "chroma"
    assert settings.upload_dir == PROJECT_ROOT / "data" / "uploads"
    assert settings.database_url == f"sqlite:///{PROJECT_ROOT / 'data' / 'app.db'}"


def test_project_root_is_the_folder_with_pyproject() -> None:
    assert (PROJECT_ROOT / "pyproject.toml").is_file()


def test_absolute_paths_are_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CHROMA_DIR", str(tmp_path / "chroma"))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'app.db'}")

    settings = Settings()

    assert settings.chroma_dir == tmp_path / "chroma"
    assert settings.database_url == f"sqlite:///{tmp_path / 'app.db'}"


@pytest.mark.parametrize(
    "url",
    [
        "sqlite://",
        "sqlite:///:memory:",
        "mysql+pymysql://user:p%40ss@localhost:3306/pka",
    ],
)
def test_non_file_database_urls_are_unchanged(
    monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    monkeypatch.setenv("DATABASE_URL", url)

    assert Settings().database_url == url


def test_api_base_url_drops_trailing_slash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_BASE_URL", "http://api.local:9000/")

    assert Settings().api_base_url == "http://api.local:9000"


def test_get_settings_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TOP_K", "9")
    first = get_settings()
    monkeypatch.setenv("TOP_K", "2")

    assert get_settings() is first
    assert first.top_k == 9
