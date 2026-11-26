import io
import zipfile
from pathlib import Path

import pytest

from ai_personal_knowledge_assistant.core.errors import (
    CorruptDocumentError,
    EmptyDocumentError,
    FileTooLargeError,
    UnsupportedFileError,
)
from ai_personal_knowledge_assistant.ingestion.validation import (
    ValidatedUpload,
    sanitize_filename,
    validate_upload,
)
from ai_personal_knowledge_assistant.storage.records import FileType
from tests.ingestion.builders import make_docx, make_pdf

LIMIT = 1_000_000


def zip_with(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return buffer.getvalue()


# --- accepted files ---


@pytest.mark.parametrize(
    ("filename", "file_type"),
    [
        ("HR_Policy.pdf", FileType.PDF),
        ("Employee_Handbook.docx", FileType.DOCX),
        ("Travel_Policy.txt", FileType.TXT),
    ],
)
def test_accepts_each_sample_document(
    sample_docs_dir: Path, filename: str, file_type: FileType
) -> None:
    content = (sample_docs_dir / filename).read_bytes()

    assert validate_upload(filename, content, LIMIT) == ValidatedUpload(
        filename, file_type
    )


@pytest.mark.parametrize(
    ("filename", "content", "file_type"),
    [
        ("REPORT.PDF", make_pdf(["hello"]), FileType.PDF),
        ("Notes.Docx", make_docx(["hello"]), FileType.DOCX),
        ("README.TXT", b"hello", FileType.TXT),
    ],
)
def test_extension_is_case_insensitive(
    filename: str, content: bytes, file_type: FileType
) -> None:
    assert validate_upload(filename, content, LIMIT).file_type is file_type


def test_accepts_txt_with_bom_and_latin1_bytes() -> None:
    assert validate_upload("a.txt", b"\xef\xbb\xbfhi", LIMIT).file_type is FileType.TXT
    assert validate_upload("b.txt", "café".encode("latin-1"), LIMIT).file_type is (
        FileType.TXT
    )


def test_accepts_file_exactly_at_the_limit() -> None:
    assert validate_upload("a.txt", b"x" * 10, max_bytes=10).file_type is FileType.TXT


# --- rejected files, in check order ---


@pytest.mark.parametrize(
    "filename", ["photo.png", "archive.zip", "noextension", ".pdf"]
)
def test_rejects_unsupported_extension(filename: str) -> None:
    with pytest.raises(UnsupportedFileError):
        validate_upload(filename, b"%PDF-1.7 whatever", LIMIT)


def test_rejects_empty_file() -> None:
    with pytest.raises(EmptyDocumentError):
        validate_upload("empty.pdf", b"", LIMIT)


def test_rejects_oversized_file_with_sizes_on_the_error() -> None:
    with pytest.raises(FileTooLargeError) as caught:
        validate_upload("big.txt", b"x" * 11, max_bytes=10)

    assert (caught.value.size_bytes, caught.value.limit_bytes) == (11, 10)


def test_extension_is_checked_before_size() -> None:
    with pytest.raises(UnsupportedFileError):
        validate_upload("big.exe", b"x" * 11, max_bytes=10)


@pytest.mark.parametrize(
    ("filename", "content"),
    [
        ("fake.pdf", b"just some text pretending to be a PDF"),
        ("fake.pdf", b"PK\x03\x04 zip bytes"),
        ("fake.docx", b"%PDF-1.7 a PDF renamed to docx"),
        ("fake.docx", b"plain text renamed to docx"),
    ],
)
def test_rejects_content_that_does_not_match_extension(
    filename: str, content: bytes
) -> None:
    with pytest.raises(UnsupportedFileError, match="content is not"):
        validate_upload(filename, content, LIMIT)


def test_rejects_zip_without_word_document_part() -> None:
    plain_zip = zip_with({"readme.txt": b"not a word file"})

    with pytest.raises(UnsupportedFileError):
        validate_upload("archive.docx", plain_zip, LIMIT)


def test_damaged_docx_container_is_corrupt() -> None:
    truncated = make_docx(["hello"])[:200]

    with pytest.raises(CorruptDocumentError):
        validate_upload("broken.docx", truncated, LIMIT)


@pytest.mark.parametrize(
    "content",
    [
        b"text with a \x00 NUL byte",
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR",
        "hello".encode("utf-16"),
    ],
)
def test_rejects_binary_content_in_txt(content: bytes) -> None:
    with pytest.raises(UnsupportedFileError):
        validate_upload("data.txt", content, LIMIT)


# --- filename sanitizing ---


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("report.pdf", "report.pdf"),
        ("../../etc/passwd.txt", "passwd.txt"),
        ("/abs/path/notes.txt", "notes.txt"),
        ("C:\\Users\\me\\Desktop\\plan.docx", "plan.docx"),
        ("..\\..\\secret.pdf", "secret.pdf"),
        ("  spaced name.pdf  ", "spaced name.pdf"),
        ("evil\x00name\n.txt", "evilname.txt"),
    ],
)
def test_sanitize_filename_keeps_only_a_safe_basename(raw: str, expected: str) -> None:
    assert sanitize_filename(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "..", "../", "some/dir/", "\x00"])
def test_sanitize_filename_rejects_names_with_no_basename(raw: str) -> None:
    with pytest.raises(UnsupportedFileError):
        sanitize_filename(raw)


def test_sanitize_filename_rejects_names_longer_than_the_stored_limit() -> None:
    assert sanitize_filename("a" * 251 + ".pdf") == "a" * 251 + ".pdf"
    with pytest.raises(UnsupportedFileError, match="limit is 255"):
        sanitize_filename("a" * 252 + ".pdf")


def test_validate_upload_returns_the_sanitized_name() -> None:
    upload = validate_upload("../../uploads/Travel.txt", b"hello", LIMIT)

    assert upload.filename == "Travel.txt"
