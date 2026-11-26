"""Upload validation: decide whether a file may enter the ingestion pipeline.

Checks run cheapest first and stop at the first failure: the extension, then
emptiness, then size, then whether the content really is what the extension
claims (magic bytes). Nothing here parses the whole document; a file that passes
can still turn out to be unreadable during extraction.
"""

import io
import re
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import PurePosixPath

from ai_personal_knowledge_assistant.core.errors import (
    CorruptDocumentError,
    EmptyDocumentError,
    FileTooLargeError,
    UnsupportedFileError,
)
from ai_personal_knowledge_assistant.ingestion.text_decoding import looks_binary
from ai_personal_knowledge_assistant.storage.records import (
    FILENAME_MAX_LENGTH,
    FileType,
)

_FILE_TYPES_BY_EXTENSION = {f".{file_type.value}": file_type for file_type in FileType}
_SUPPORTED_LIST = ", ".join(ext.upper().lstrip(".") for ext in _FILE_TYPES_BY_EXTENSION)

_PDF_MAGIC = b"%PDF-"
_ZIP_MAGIC = b"PK\x03\x04"
_DOCX_MAIN_PART = "word/document.xml"

_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(frozen=True, slots=True)
class ValidatedUpload:
    """An upload that passed validation: a safe display name and its real type."""

    filename: str
    file_type: FileType


def sanitize_filename(filename: str) -> str:
    """Reduce a client-supplied name to a bare, safe basename.

    Directory parts are dropped whichever separator the client used, so
    `../../etc/passwd` and `C:\\docs\\a.pdf` become `passwd` and `a.pdf`.
    Control characters are removed. The result is meant for display and
    metadata; files on disk should still be stored under generated names.
    """
    # Not PurePosixPath.name: that maps "dir/" to "dir", but a name ending in a
    # separator has no file part at all.
    basename = filename.replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = _CONTROL_CHARACTERS.sub("", basename).strip()
    if cleaned in ("", ".", ".."):
        raise UnsupportedFileError(f"{filename!r} is not a usable file name.")
    if len(cleaned) > FILENAME_MAX_LENGTH:
        raise UnsupportedFileError(
            f"File name is {len(cleaned)} characters; "
            f"the limit is {FILENAME_MAX_LENGTH}. Rename the file and try again."
        )
    return cleaned


def validate_upload(filename: str, content: bytes, max_bytes: int) -> ValidatedUpload:
    """Check an upload and return its sanitized name and detected type.

    Raises UnsupportedFileError, EmptyDocumentError, FileTooLargeError, or
    CorruptDocumentError (a DOCX whose ZIP container is damaged).
    """
    safe_name = sanitize_filename(filename)
    file_type = _file_type_from_extension(safe_name)
    if not content:
        raise EmptyDocumentError(f"{safe_name!r} is empty.")
    if len(content) > max_bytes:
        raise FileTooLargeError(len(content), max_bytes)
    _CONTENT_CHECKS[file_type](safe_name, content)
    return ValidatedUpload(filename=safe_name, file_type=file_type)


def _file_type_from_extension(filename: str) -> FileType:
    extension = PurePosixPath(filename).suffix.lower()
    try:
        return _FILE_TYPES_BY_EXTENSION[extension]
    except KeyError:
        raise UnsupportedFileError(
            f"{filename!r} is not a supported file type. "
            f"Upload one of: {_SUPPORTED_LIST}."
        ) from None


def _check_pdf(filename: str, content: bytes) -> None:
    if not content.startswith(_PDF_MAGIC):
        raise _content_mismatch(filename, FileType.PDF)


def _check_docx(filename: str, content: bytes) -> None:
    if not content.startswith(_ZIP_MAGIC):
        raise _content_mismatch(filename, FileType.DOCX)
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            part_names = set(archive.namelist())
    except zipfile.BadZipFile as error:
        raise CorruptDocumentError(
            f"{filename!r} looks like a DOCX file but is damaged ({error})."
        ) from error
    if _DOCX_MAIN_PART not in part_names:
        raise _content_mismatch(filename, FileType.DOCX)


def _check_txt(filename: str, content: bytes) -> None:
    if looks_binary(content):
        raise _content_mismatch(filename, FileType.TXT)


def _content_mismatch(filename: str, claimed: FileType) -> UnsupportedFileError:
    return UnsupportedFileError(
        f"{filename!r} has a .{claimed.value} extension, "
        f"but its content is not a {claimed.value.upper()} file."
    )


_CONTENT_CHECKS: dict[FileType, Callable[[str, bytes], None]] = {
    FileType.PDF: _check_pdf,
    FileType.DOCX: _check_docx,
    FileType.TXT: _check_txt,
}
