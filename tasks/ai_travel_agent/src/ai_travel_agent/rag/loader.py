"""Turn an uploaded file into pages of text: PDF (PyMuPDF), DOCX, TXT.

The file type is decided from the extension *and* checked against the content,
so a renamed executable or a corrupt file is rejected before parsing. PDF pages
keep their real 1-based number for citations; DOCX and TXT have no stored pages
and become one page with `number=None`.

Document text is data, never instructions: it is only ever passed to the model
as quoted evidence (see agents/prompts.py).
"""

import io
import re
import zipfile
from dataclasses import dataclass
from enum import StrEnum

import pymupdf
from docx import Document as open_docx
from docx.opc.exceptions import PackageNotFoundError

from ai_travel_agent.exceptions.errors import (
    InvalidDocumentError,
    UnsupportedFileError,
)

_HORIZONTAL_WHITESPACE = re.compile(r"[^\S\n]+")
_EXCESS_BLANK_LINES = re.compile(r"\n{3,}")


class FileType(StrEnum):
    PDF = "pdf"
    DOCX = "docx"
    TXT = "txt"


@dataclass(frozen=True, slots=True)
class Page:
    """Text from one page; `number` is 1-based, or None for unpaginated files."""

    number: int | None
    text: str


def detect_file_type(filename: str, data: bytes) -> FileType:
    """The file's type, or `UnsupportedFileError` if the extension isn't PDF,
    DOCX or TXT or the content doesn't match it."""
    suffix = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    try:
        file_type = FileType(suffix)
    except ValueError:
        raise UnsupportedFileError(
            "Only PDF, DOCX and TXT documents can be uploaded."
        ) from None
    matches = {
        FileType.PDF: data.startswith(b"%PDF-"),
        FileType.DOCX: data.startswith(b"PK\x03\x04"),
        FileType.TXT: b"\x00" not in data[:4096],
    }[file_type]
    if not matches:
        raise UnsupportedFileError(
            f"The file's content isn't a valid {file_type.value.upper()} document."
        )
    return file_type


def extract_pages(data: bytes, file_type: FileType) -> list[Page]:
    """The non-empty pages of the document, whitespace-normalized."""
    match file_type:
        case FileType.PDF:
            pages = _pdf_pages(data)
        case FileType.DOCX:
            pages = _single_page(_docx_text(data))
        case FileType.TXT:
            pages = _single_page(_decode_text(data))
    if not pages:
        raise InvalidDocumentError(
            "No text could be extracted from this document (a scanned PDF needs "
            "a text layer)."
        )
    return pages


def normalize_whitespace(text: str) -> str:
    """Collapse runs of spaces, trim lines, keep single blank lines between
    paragraphs so the chunker can still see paragraph breaks."""
    unified = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = (
        _HORIZONTAL_WHITESPACE.sub(" ", line).strip() for line in unified.split("\n")
    )
    return _EXCESS_BLANK_LINES.sub("\n\n", "\n".join(lines)).strip()


def _pdf_pages(data: bytes) -> list[Page]:
    try:
        with pymupdf.open(stream=data, filetype="pdf") as document:
            if document.needs_pass:
                raise InvalidDocumentError("The PDF is password-protected.")
            texts = [page.get_text() for page in document]
    except (pymupdf.FileDataError, RuntimeError, ValueError) as error:
        raise InvalidDocumentError(f"The PDF could not be read: {error}") from error
    return [
        Page(number, normalized)
        for number, text in enumerate(texts, start=1)
        if (normalized := normalize_whitespace(text))
    ]


def _docx_text(data: bytes) -> str:
    try:
        document = open_docx(io.BytesIO(data))
    except (PackageNotFoundError, zipfile.BadZipFile, KeyError, ValueError) as error:
        raise InvalidDocumentError(f"The DOCX could not be read: {error}") from error
    blocks: list[str] = []
    for block in document.iter_inner_content():
        if hasattr(block, "rows"):  # a table: one line per row, cells joined
            for row in block.rows:
                cells = dict.fromkeys(cell.text.strip() for cell in row.cells)
                blocks.append(" | ".join(text for text in cells if text))
        else:
            blocks.append(block.text)
    return "\n\n".join(blocks)


def _decode_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def _single_page(text: str) -> list[Page]:
    normalized = normalize_whitespace(text)
    return [Page(None, normalized)] if normalized else []
