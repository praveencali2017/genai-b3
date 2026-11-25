"""Shared test fixtures."""

from collections.abc import Iterator

import pytest

from ai_personal_knowledge_assistant.core.settings import Settings, get_settings

SETTING_NAMES = [name.upper() for name in Settings.model_fields]


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Never read the developer's shell variables or real `.env` file."""
    for name in SETTING_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
