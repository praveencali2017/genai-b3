"""OCR wiring: the startup check, and scanned-PDF uploads through the API."""

import logging
from collections.abc import Callable
from contextlib import AbstractContextManager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ai_personal_knowledge_assistant.api.container import (
    AppContainer,
    PageOcrFactory,
    open_container,
)
from ai_personal_knowledge_assistant.api.main import create_app
from ai_personal_knowledge_assistant.core.errors import (
    OcrFailedError,
    OcrUnavailableError,
)
from ai_personal_knowledge_assistant.core.settings import Settings
from ai_personal_knowledge_assistant.ingestion.ocr import PageOcr
from tests.fakes import FakeEmbedder, FakePageOcr
from tests.ingestion.builders import make_pdf

# Blank pages have no text layer, so extraction treats them like scanned pages.
SCANNED_PDF = make_pdf(["", ""])
PARTLY_SCANNED_PDF = make_pdf(["", "Typed page two."])


def app_with_ocr(settings: Settings, page_ocr_factory: PageOcrFactory) -> FastAPI:
    def open_test_container() -> AbstractContextManager[AppContainer]:
        return open_container(
            settings, embedder=FakeEmbedder(), page_ocr_factory=page_ocr_factory
        )

    return create_app(open_test_container)


def returning(page_ocr: PageOcr) -> Callable[[], PageOcr]:
    return lambda: page_ocr


def unavailable() -> PageOcr:
    raise OcrUnavailableError("the programs pdftoppm (poppler) are not on PATH.")


def upload_scan(
    client: TestClient, content: bytes = SCANNED_PDF
) -> tuple[int, dict[str, object]]:
    response = client.post("/documents/upload", files={"file": ("scan.pdf", content)})
    return response.status_code, response.json()


def running_container(client: TestClient) -> AppContainer:
    return client.app.state.container  # type: ignore[attr-defined]


@pytest.fixture
def ocr_settings(settings: Settings) -> Settings:
    return settings.model_copy(update={"ocr_enabled": True})


def test_ocr_disabled_never_loads_ocr_and_rejects_scans_with_a_hint(
    settings: Settings,
) -> None:
    def must_not_load() -> PageOcr:
        raise AssertionError("OCR must not be loaded when OCR_ENABLED is false")

    with TestClient(app_with_ocr(settings, must_not_load)) as client:
        status_code, body = upload_scan(client)
        assert running_container(client).page_ocr is None

    assert status_code == 422
    assert "needs OCR, which is not enabled" in str(body["detail"])


def test_ocr_enabled_reads_scanned_pages_on_upload(ocr_settings: Settings) -> None:
    ocr = FakePageOcr({1: "Scanned page one."})

    with TestClient(app_with_ocr(ocr_settings, returning(ocr))) as client:
        status_code, body = upload_scan(client, PARTLY_SCANNED_PDF)

    assert status_code == 201, body
    assert body["page_count"] == 2
    assert ocr.pages_read == [1]


def test_unavailable_ocr_is_logged_at_startup_and_turned_off(
    ocr_settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    with (
        caplog.at_level(logging.ERROR),
        TestClient(app_with_ocr(ocr_settings, unavailable)) as client,
    ):
        assert running_container(client).page_ocr is None
        status_code, body = upload_scan(client)

    (record,) = [r for r in caplog.records if r.name.endswith("api.container")]
    assert record.levelno == logging.ERROR
    assert "pdftoppm (poppler)" in record.getMessage()
    assert status_code == 422
    assert "needs OCR" in str(body["detail"])


@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        (OcrFailedError("OCR failed on page 1 of the PDF (bad image)."), 422),
        (OcrUnavailableError("tesseract is not installed."), 503),
    ],
)
def test_ocr_errors_during_upload_are_clear_and_store_nothing(
    ocr_settings: Settings, error: Exception, expected_status: int
) -> None:
    ocr = FakePageOcr(error=error)

    with TestClient(app_with_ocr(ocr_settings, returning(ocr))) as client:
        status_code, body = upload_scan(client)
        documents = client.get("/documents").json()

    assert status_code == expected_status
    assert body == {"detail": str(error)}
    assert documents == []
