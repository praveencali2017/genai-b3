"""Fixtures shared by every test.

Tests never read the developer's `.env` or environment: `isolated_env` removes
every setting from the environment and disables the env file, so each test
builds exactly the `Settings` it needs.
"""

from collections.abc import Iterator

import pytest

from ai_travel_agent.config.settings import Settings, get_settings
from tests.fakes import FakeChatClient, FakeEmbeddingClient


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def fake_chat() -> FakeChatClient:
    return FakeChatClient()


@pytest.fixture
def fake_embeddings() -> FakeEmbeddingClient:
    return FakeEmbeddingClient()
