import io
from pathlib import Path

import pytest
from pypdf import PdfWriter

from ai_personal_knowledge_assistant.core.errors import (
    CorruptDocumentError,
    EmptyDocumentError,
)
from ai_personal_knowledge_assistant.ingestion.extractors import (
    Page,
    extract_pages,
    normalize_whitespace,
)
from ai_personal_knowledge_assistant.storage.records import FileType
from tests.ingestion.builders import make_docx, make_pdf

# --- PDF ---


def test_sample_pdf_has_numbered_pages_with_casual_leave_on_page_2(
    sample_docs_dir: Path,
) -> None:
    pages = extract_pages(sample_docs_dir / "HR_Policy.pdf", FileType.PDF)

    assert [page.number for page in pages] == [1, 2, 3]
    pages_mentioning_casual_leave = [
        page.number for page in pages if "casual leave" in page.text.lower()
    ]
    assert pages_mentioning_casual_leave == [2]
    assert "12 days of casual leave" in pages[1].text


def test_accepts_bytes_as_well_as_a_path(sample_docs_dir: Path) -> None:
    path = sample_docs_dir / "HR_Policy.pdf"

    assert extract_pages(path.read_bytes(), FileType.PDF) == extract_pages(
        path, FileType.PDF
    )


def test_blank_pdf_pages_are_skipped_but_numbering_is_kept() -> None:
    pdf = make_pdf(["First page.", "", "Third page."])

    pages = extract_pages(pdf, FileType.PDF)

    assert pages == [Page(1, "First page."), Page(3, "Third page.")]


def test_pdf_without_any_text_is_empty_and_hints_at_ocr() -> None:
    blank_pdf = make_pdf(["", ""])

    with pytest.raises(EmptyDocumentError, match="OCR"):
        extract_pages(blank_pdf, FileType.PDF)


def test_truncated_pdf_is_corrupt(sample_docs_dir: Path) -> None:
    content = (sample_docs_dir / "HR_Policy.pdf").read_bytes()

    with pytest.raises(CorruptDocumentError):
        extract_pages(content[: len(content) // 3], FileType.PDF)


def test_garbage_after_pdf_header_is_corrupt() -> None:
    with pytest.raises(CorruptDocumentError):
        extract_pages(b"%PDF-1.7\nthis is not really a pdf", FileType.PDF)


def test_password_protected_pdf_is_corrupt(tmp_path: Path) -> None:
    writer = PdfWriter(clone_from=io.BytesIO(make_pdf(["Secret."])))
    writer.encrypt(user_password="hunter2", algorithm="RC4-128")
    path = tmp_path / "locked.pdf"
    writer.write(path)

    with pytest.raises(CorruptDocumentError, match="password"):
        extract_pages(path, FileType.PDF)


def test_pdf_encrypted_with_empty_user_password_is_readable(tmp_path: Path) -> None:
    writer = PdfWriter(clone_from=io.BytesIO(make_pdf(["Readable."])))
    writer.encrypt(user_password="", owner_password="owner", algorithm="RC4-128")
    path = tmp_path / "restricted.pdf"
    writer.write(path)

    assert extract_pages(path, FileType.PDF) == [Page(1, "Readable.")]


# --- DOCX ---


def test_sample_docx_is_one_unnumbered_page_including_table_text(
    sample_docs_dir: Path,
) -> None:
    pages = extract_pages(sample_docs_dir / "Employee_Handbook.docx", FileType.DOCX)

    assert len(pages) == 1
    assert pages[0].number is None
    assert "IT and Security Policy" in pages[0].text
    assert "Learning budget | USD 1,000 per year" in pages[0].text


def test_docx_keeps_paragraph_and_table_order() -> None:
    docx = make_docx(
        ["Before the table.", "", "Still before."],
        table=[["Name", "Days"], ["Casual", "12"]],
    )

    pages = extract_pages(docx, FileType.DOCX)

    assert pages == [
        Page(None, "Before the table.\n\nStill before.\n\nName | Days\nCasual | 12")
    ]


def test_docx_without_text_is_empty() -> None:
    with pytest.raises(EmptyDocumentError):
        extract_pages(make_docx(["", "   "]), FileType.DOCX)


def test_damaged_docx_is_corrupt() -> None:
    with pytest.raises(CorruptDocumentError):
        extract_pages(make_docx(["hello"])[:300], FileType.DOCX)


# --- TXT ---


def test_sample_txt_is_one_unnumbered_page(sample_docs_dir: Path) -> None:
    pages = extract_pages(sample_docs_dir / "Travel_Policy.txt", FileType.TXT)

    assert len(pages) == 1
    assert pages[0].number is None
    assert "USD 60" in pages[0].text


def test_txt_utf8_bom_is_dropped() -> None:
    content = "﻿Café policy".encode()

    assert extract_pages(content, FileType.TXT) == [Page(None, "Café policy")]


def test_txt_falls_back_to_latin1() -> None:
    content = "Café – naïve".encode("latin-1", errors="replace")

    assert extract_pages(content, FileType.TXT)[0].text == "Café ? naïve"


def test_whitespace_only_txt_is_empty() -> None:
    with pytest.raises(EmptyDocumentError):
        extract_pages(b" \n\t\r\n ", FileType.TXT)


# --- whitespace normalization ---


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("a   b\t\tc", "a b c"),
        ("  line one  \n  line two  ", "line one\nline two"),
        ("para one\n\n\n\n\npara two", "para one\n\npara two"),
        ("para one\n   \n\t\npara two", "para one\n\npara two"),
        ("windows\r\nline\rendings", "windows\nline\nendings"),
        ("no break", "no break"),
        ("\n\n  \n", ""),
    ],
)
def test_normalize_whitespace(raw: str, expected: str) -> None:
    assert normalize_whitespace(raw) == expected
