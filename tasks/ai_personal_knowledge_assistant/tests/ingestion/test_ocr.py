"""OCR of PDF pages without a text layer: the extraction fallback, the
availability check, and one run against the real tesseract and poppler."""

import shutil
import sys

import pytest

from ai_personal_knowledge_assistant.core.errors import (
    EmptyDocumentError,
    OcrFailedError,
    OcrUnavailableError,
)
from ai_personal_knowledge_assistant.ingestion import ocr as ocr_module
from ai_personal_knowledge_assistant.ingestion.extractors import Page, extract_pages
from ai_personal_knowledge_assistant.ingestion.ocr import TesseractPageOcr
from ai_personal_knowledge_assistant.storage.records import FileType
from tests.fakes import FakePageOcr
from tests.ingestion.builders import make_docx, make_pdf, make_scanned_pdf

# --- Extraction with OCR (fake engine) ---


def test_pages_without_text_are_read_by_ocr_and_keep_their_page_numbers() -> None:
    pdf = make_pdf(["", "Typed page two.", "", ""])
    ocr = FakePageOcr({1: "Scanned  page one.\n", 3: "Scanned page three."})

    pages = extract_pages(pdf, FileType.PDF, ocr)

    assert pages == [
        Page(1, "Scanned page one."),
        Page(2, "Typed page two."),
        Page(3, "Scanned page three."),
    ]


def test_only_pages_without_text_are_sent_to_ocr() -> None:
    pdf = make_pdf(["Typed page one.", "", "Typed page three.", ""])
    ocr = FakePageOcr({2: "Scanned page two."})

    extract_pages(pdf, FileType.PDF, ocr)

    assert ocr.pages_read == [2, 4]


def test_a_pdf_that_has_text_everywhere_never_calls_ocr() -> None:
    ocr = FakePageOcr()

    pages = extract_pages(make_pdf(["One.", "Two."]), FileType.PDF, ocr)

    assert pages == [Page(1, "One."), Page(2, "Two.")]
    assert ocr.pages_read == []


def test_without_ocr_a_scanned_pdf_is_empty_and_hints_at_enabling_ocr() -> None:
    with pytest.raises(EmptyDocumentError, match="needs OCR, which is not enabled"):
        extract_pages(make_scanned_pdf(["Hello"]), FileType.PDF, ocr=None)


@pytest.mark.parametrize("ocr_text", ["", "  \n\t "])
def test_a_scan_that_ocr_finds_no_text_in_is_empty(ocr_text: str) -> None:
    ocr = FakePageOcr({1: ocr_text, 2: ocr_text})

    with pytest.raises(EmptyDocumentError, match="even with OCR"):
        extract_pages(make_pdf(["", ""]), FileType.PDF, ocr)


def test_an_ocr_failure_propagates() -> None:
    ocr = FakePageOcr(error=OcrFailedError("OCR failed on page 1"))

    with pytest.raises(OcrFailedError, match="page 1"):
        extract_pages(make_pdf([""]), FileType.PDF, ocr)


def test_ocr_is_not_used_for_formats_other_than_pdf() -> None:
    ocr = FakePageOcr({1: "should not appear"})

    with pytest.raises(EmptyDocumentError, match="DOCX file contains no text"):
        extract_pages(make_docx([""]), FileType.DOCX, ocr)
    assert ocr.pages_read == []


# --- Availability check ---


def _require_ocr_packages() -> None:
    pytest.importorskip("pytesseract", reason="needs the `ocr` extra")
    pytest.importorskip("pdf2image", reason="needs the `ocr` extra")


def test_load_reports_missing_python_packages(monkeypatch: pytest.MonkeyPatch) -> None:
    # A None entry in sys.modules makes `import pytesseract` raise ImportError.
    monkeypatch.setitem(sys.modules, "pytesseract", None)

    with pytest.raises(OcrUnavailableError, match="pytesseract") as caught:
        TesseractPageOcr.load()

    assert "uv sync --all-packages --extra ocr" in str(caught.value)


def test_load_reports_missing_programs(monkeypatch: pytest.MonkeyPatch) -> None:
    _require_ocr_packages()
    monkeypatch.setattr(ocr_module.shutil, "which", lambda _program: None)

    with pytest.raises(OcrUnavailableError) as caught:
        TesseractPageOcr.load()

    message = str(caught.value)
    assert "tesseract (tesseract)" in message
    assert "pdftoppm (poppler)" in message
    assert "brew install tesseract poppler" in message


def test_load_succeeds_when_everything_is_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _require_ocr_packages()
    monkeypatch.setattr(
        ocr_module.shutil, "which", lambda program: f"/usr/bin/{program}"
    )

    assert isinstance(TesseractPageOcr.load(), TesseractPageOcr)


# --- TesseractPageOcr with the libraries stubbed out ---


@pytest.fixture
def stub_ocr_libraries(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """Replace pdf2image's renderer and pytesseract's reader with recorders."""
    _require_ocr_packages()
    import pdf2image
    import pytesseract

    calls: dict[str, object] = {}
    rendered_image = object()

    def convert_from_bytes(pdf: bytes, **options: object) -> list[object]:
        calls["render"] = options
        return [rendered_image]

    def image_to_string(image: object, **options: object) -> str:
        calls["recognized_image_is_rendered_page"] = image is rendered_image
        return "Recognized text"

    monkeypatch.setattr(pdf2image, "convert_from_bytes", convert_from_bytes)
    monkeypatch.setattr(pytesseract, "image_to_string", image_to_string)
    return calls


def test_read_page_renders_only_the_requested_page_and_reads_it(
    stub_ocr_libraries: dict[str, object],
) -> None:
    text = TesseractPageOcr().read_page(b"%PDF-1.7 ...", 3)

    assert text == "Recognized text"
    render_options = stub_ocr_libraries["render"]
    assert isinstance(render_options, dict)
    assert render_options["first_page"] == render_options["last_page"] == 3
    assert render_options["dpi"] == 300
    assert stub_ocr_libraries["recognized_image_is_rendered_page"] is True


def _failing(error: Exception) -> object:
    def fail(*_args: object, **_kwargs: object) -> object:
        raise error

    return fail


@pytest.mark.parametrize(
    ("library", "function_name", "error_name", "expected_error"),
    [
        (
            "pdf2image",
            "convert_from_bytes",
            "PDFInfoNotInstalledError",
            OcrUnavailableError,
        ),
        ("pdf2image", "convert_from_bytes", "PDFSyntaxError", OcrFailedError),
        ("pdf2image", "convert_from_bytes", "PDFPopplerTimeoutError", OcrFailedError),
        (
            "pytesseract",
            "image_to_string",
            "TesseractNotFoundError",
            OcrUnavailableError,
        ),
        ("pytesseract", "image_to_string", "TesseractError", OcrFailedError),
    ],
)
def test_read_page_translates_library_errors(
    stub_ocr_libraries: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
    library: str,
    function_name: str,
    error_name: str,
    expected_error: type[Exception],
) -> None:
    import pdf2image.exceptions
    import pytesseract

    error_class = getattr(
        {"pdf2image": pdf2image.exceptions, "pytesseract": pytesseract}[library],
        error_name,
    )
    error = error_class(1, "boom") if error_name == "TesseractError" else error_class()
    monkeypatch.setattr(sys.modules[library], function_name, _failing(error))

    with pytest.raises(expected_error) as caught:
        TesseractPageOcr().read_page(b"%PDF-1.7 ...", 4)

    assert caught.value.__cause__ is error
    if expected_error is OcrFailedError:
        assert "page 4" in str(caught.value).lower()


# --- Real tesseract and poppler ---

_MISSING_PROGRAMS = [
    program
    for program in ("tesseract", "pdftoppm", "pdfinfo")
    if shutil.which(program) is None
]


@pytest.mark.skipif(
    bool(_MISSING_PROGRAMS),
    reason=f"needs tesseract and poppler; missing: {', '.join(_MISSING_PROGRAMS)}",
)
def test_real_ocr_reads_a_scanned_page_and_keeps_its_page_number() -> None:
    _require_ocr_packages()
    pdf = make_scanned_pdf(["", "Casual leave is twelve days"])

    pages = extract_pages(pdf, FileType.PDF, TesseractPageOcr.load())

    assert [page.number for page in pages] == [2]
    assert "casual leave is twelve days" in pages[0].text.lower()
