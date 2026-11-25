"""Text extraction: turn a validated file into pages of normalized text.

Each extractor returns only pages that contain text. PDF pages keep their real
1-based number even when blank pages around them are dropped, so citations
point at the right page.

DOCX files have no stored pages (pagination is decided by whatever renders the
file), so a DOCX becomes one `Page` with `number=None`. Explicit page breaks are
deliberately ignored: they are rare, and numbering by them would disagree with
the page numbers Word shows whenever text flows onto a new page on its own.
Plain text likewise becomes one unnumbered page.
"""

import io
import re
import zipfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

from docx import Document as open_docx
from docx.opc.exceptions import PackageNotFoundError
from docx.table import Table
from docx.text.paragraph import Paragraph
from pypdf import PdfReader
from pypdf.errors import PyPdfError

from ai_personal_knowledge_assistant.core.errors import (
    CorruptDocumentError,
    EmptyDocumentError,
)
from ai_personal_knowledge_assistant.ingestion.text_decoding import decode_text
from ai_personal_knowledge_assistant.storage.records import FileType

_HORIZONTAL_WHITESPACE = re.compile(r"[^\S\n]+")
_EXCESS_BLANK_LINES = re.compile(r"\n{3,}")
_TABLE_CELL_SEPARATOR = " | "


@dataclass(frozen=True, slots=True)
class Page:
    """Text from one page; `number` is 1-based, or None for unpaginated formats."""

    number: int | None
    text: str


def extract_pages(content: bytes | Path, file_type: FileType) -> list[Page]:
    """Extract the non-empty pages of a document.

    Raises CorruptDocumentError if the file can't be parsed (damaged or
    password-protected) and EmptyDocumentError if it contains no text at all.
    """
    data = content.read_bytes() if isinstance(content, Path) else content
    pages = _EXTRACTORS[file_type](data)
    if not pages:
        raise EmptyDocumentError(_NO_TEXT_MESSAGES[file_type])
    return pages


def normalize_whitespace(text: str) -> str:
    """Collapse runs of spaces/tabs, trim lines, and keep at most one blank line
    between paragraphs, so paragraph breaks survive for the chunker."""
    unified = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = (
        _HORIZONTAL_WHITESPACE.sub(" ", line).strip() for line in unified.split("\n")
    )
    return _EXCESS_BLANK_LINES.sub("\n\n", "\n".join(lines)).strip()


def _non_empty_page(number: int | None, raw_text: str) -> list[Page]:
    text = normalize_whitespace(raw_text)
    return [Page(number=number, text=text)] if text else []


# --- PDF ---


def _extract_pdf(data: bytes) -> list[Page]:
    # pypdf signals malformed input with a wide range of exception types, not
    # only its own; any of them means the same thing to the user.
    try:
        reader = PdfReader(io.BytesIO(data))
        _unlock_if_encrypted(reader)
        return [
            page
            for number, pdf_page in enumerate(reader.pages, start=1)
            for page in _non_empty_page(number, pdf_page.extract_text() or "")
        ]
    except (PyPdfError, ValueError, KeyError, TypeError, AttributeError) as error:
        raise CorruptDocumentError(
            f"The PDF could not be read; it may be damaged ({error})."
        ) from error


def _unlock_if_encrypted(reader: PdfReader) -> None:
    # Many PDFs are "encrypted" only to restrict printing or editing and open
    # with an empty password; only those that need a real password are refused.
    if reader.is_encrypted and not reader.decrypt(""):
        raise CorruptDocumentError(
            "The PDF is password-protected. Remove the password and upload it again."
        )


# --- DOCX ---


def _extract_docx(data: bytes) -> list[Page]:
    try:
        document = open_docx(io.BytesIO(data))
    except (PackageNotFoundError, zipfile.BadZipFile, KeyError, ValueError) as error:
        raise CorruptDocumentError(
            f"The DOCX file could not be read; it may be damaged ({error})."
        ) from error
    blocks = (_block_text(block) for block in document.iter_inner_content())
    return _non_empty_page(None, "\n\n".join(blocks))


def _block_text(block: Paragraph | Table) -> str:
    if isinstance(block, Paragraph):
        return block.text
    return "\n".join(_table_rows(block))


def _table_rows(table: Table) -> Iterator[str]:
    for row in table.rows:
        # A merged cell is returned once per grid column it spans; keep it once.
        cell_texts = list(dict.fromkeys(cell.text.strip() for cell in row.cells))
        yield _TABLE_CELL_SEPARATOR.join(text for text in cell_texts if text)


# --- TXT ---


def _extract_txt(data: bytes) -> list[Page]:
    return _non_empty_page(None, decode_text(data))


_EXTRACTORS: dict[FileType, Callable[[bytes], list[Page]]] = {
    FileType.PDF: _extract_pdf,
    FileType.DOCX: _extract_docx,
    FileType.TXT: _extract_txt,
}

_NO_TEXT_MESSAGES: dict[FileType, str] = {
    FileType.PDF: (
        "No text could be extracted from this PDF. If it is a scanned document, "
        "it needs OCR, which is not enabled."
    ),
    FileType.DOCX: "This DOCX file contains no text.",
    FileType.TXT: "This text file contains no text.",
}
