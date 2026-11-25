"""Fixtures for the ingestion tests."""

from pathlib import Path

import pytest

SAMPLE_DOCS_DIR = Path(__file__).resolve().parents[2] / "sample_docs"


@pytest.fixture
def sample_docs_dir() -> Path:
    return SAMPLE_DOCS_DIR
