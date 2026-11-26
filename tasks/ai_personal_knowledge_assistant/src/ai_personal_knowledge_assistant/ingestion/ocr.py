"""Optional OCR for PDF pages that have no text layer (scans).

`PageOcr` is the boundary extraction depends on; `TesseractPageOcr` implements it
with pdf2image (which runs poppler's `pdftoppm`) and pytesseract (which runs the
`tesseract` program). Both Python packages come from the optional `ocr` extra and
are imported lazily, so the application imports and runs without them; only
`TesseractPageOcr.load()` needs them.
"""

import importlib
import shutil
from typing import Protocol

from ai_personal_knowledge_assistant.core.errors import (
    OcrFailedError,
    OcrUnavailableError,
)

# 300 dpi is the resolution Tesseract is tuned for; lower loses small print.
_RENDER_DPI = 300
_LANGUAGE = "eng"
# Per page, for rendering and for recognition each, so one pathological page
# can't hold an upload request (and its worker thread) indefinitely.
_TIMEOUT_SECONDS = 120

_REQUIRED_MODULES = ("pytesseract", "pdf2image")
# pdf2image calls `pdfinfo` to count pages and `pdftoppm` to render them.
_REQUIRED_PROGRAMS = {
    "tesseract": "tesseract",
    "pdfinfo": "poppler",
    "pdftoppm": "poppler",
}


class PageOcr(Protocol):
    """Reads the text of one page of a PDF from its rendered image."""

    def read_page(self, pdf: bytes, page_number: int) -> str:
        """The recognized text of 1-based `page_number`; may be empty.

        Raises OcrFailedError if the page can't be rendered or recognized, and
        OcrUnavailableError if the OCR programs have gone missing.
        """
        ...


class TesseractPageOcr:
    """`PageOcr` backed by poppler (rendering) and Tesseract (recognition).

    Build it with `load()`, which checks that everything it needs is installed.
    """

    @classmethod
    def load(cls) -> "TesseractPageOcr":
        """Check the `ocr` extra and the tesseract/poppler programs are present.

        Raises OcrUnavailableError naming what is missing and how to install it.
        """
        missing_modules = [name for name in _REQUIRED_MODULES if not _importable(name)]
        if missing_modules:
            raise OcrUnavailableError(
                f"the Python packages {', '.join(missing_modules)} are not "
                "installed. Run `uv sync --all-packages --extra ocr` from the "
                "repository root."
            )
        missing_programs = sorted(
            {
                f"{program} ({package})"
                for program, package in _REQUIRED_PROGRAMS.items()
                if shutil.which(program) is None
            }
        )
        if missing_programs:
            raise OcrUnavailableError(
                f"the programs {', '.join(missing_programs)} are not on PATH. "
                "Install them, e.g. `brew install tesseract poppler` on macOS or "
                "`apt install tesseract-ocr poppler-utils` on Debian/Ubuntu."
            )
        return cls()

    def read_page(self, pdf: bytes, page_number: int) -> str:
        import pdf2image
        import pdf2image.exceptions as pdf2image_errors
        import pytesseract

        try:
            images = pdf2image.convert_from_bytes(
                pdf,
                dpi=_RENDER_DPI,
                first_page=page_number,
                last_page=page_number,
                grayscale=True,
                timeout=_TIMEOUT_SECONDS,
            )
        except (
            pdf2image_errors.PDFInfoNotInstalledError,
            pdf2image_errors.PopplerNotInstalledError,
        ) as error:
            raise OcrUnavailableError(f"poppler is not installed ({error}).") from error
        except (
            pdf2image_errors.PDFPageCountError,
            pdf2image_errors.PDFSyntaxError,
            pdf2image_errors.PDFPopplerTimeoutError,
        ) as error:
            raise OcrFailedError(
                f"Page {page_number} of the PDF could not be rendered for OCR "
                f"({error})."
            ) from error
        if not images:
            raise OcrFailedError(
                f"Page {page_number} of the PDF could not be rendered for OCR."
            )

        try:
            return pytesseract.image_to_string(
                images[0], lang=_LANGUAGE, timeout=_TIMEOUT_SECONDS
            )
        except pytesseract.TesseractNotFoundError as error:
            raise OcrUnavailableError(
                f"tesseract is not installed ({error})."
            ) from error
        # TesseractError is a RuntimeError; pytesseract also raises a bare
        # RuntimeError when the timeout is hit.
        except RuntimeError as error:
            raise OcrFailedError(
                f"OCR failed on page {page_number} of the PDF ({error})."
            ) from error


def _importable(module_name: str) -> bool:
    try:
        importlib.import_module(module_name)
    except ImportError:
        return False
    return True
