"""Tests for reading MySQL connection settings, focused on DB_PORT."""

import pytest

import db_config

REQUIRED_SETTINGS = {
    "DB_HOST": "db.example.com",
    "DB_USER": "app",
    "DB_PASSWORD": "secret",
    "DB_NAME": "app_db",
}


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Use only the variables each test sets; never read a real .env file."""
    monkeypatch.setattr(db_config, "load_dotenv", lambda *args, **kwargs: None)
    for name, value in REQUIRED_SETTINGS.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("DB_PORT", raising=False)


def test_port_defaults_to_mysql_standard_when_unset() -> None:
    assert db_config.load_db_config()["port"] == 3306


def test_blank_port_is_treated_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DB_PORT", "  ")

    assert db_config.load_db_config()["port"] == 3306


def test_custom_port_is_passed_as_an_int(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DB_PORT", " 21542 ")

    config = db_config.load_db_config()

    assert config == {
        "host": "db.example.com",
        "port": 21542,
        "user": "app",
        "password": "secret",
        "database": "app_db",
    }


@pytest.mark.parametrize("port", ["1", "65535"])
def test_port_range_boundaries_are_accepted(
    monkeypatch: pytest.MonkeyPatch, port: str
) -> None:
    monkeypatch.setenv("DB_PORT", port)

    assert db_config.load_db_config()["port"] == int(port)


@pytest.mark.parametrize("port", ["abc", "33.06", "0", "-1", "65536", "3306x"])
def test_invalid_port_fails_with_a_clear_error(
    monkeypatch: pytest.MonkeyPatch, port: str
) -> None:
    monkeypatch.setenv("DB_PORT", port)

    with pytest.raises(RuntimeError, match=r"DB_PORT must be a whole number from 1 to 65535"):
        db_config.load_db_config()


def test_missing_required_settings_are_still_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DB_HOST")

    with pytest.raises(RuntimeError, match="DB_HOST"):
        db_config.load_db_config()


def test_env_file_is_found_in_a_parent_folder(tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("DB_HOST=x\n")
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)

    assert db_config.find_env_file(nested) == env_file


def test_missing_env_file_is_not_an_error_at_a_shallow_path(tmp_path) -> None:
    """On Vercel the app sits at /var/task with no .env; settings come from the environment."""
    shallow = tmp_path / "task"
    shallow.mkdir()

    assert db_config.find_env_file(shallow) is None
