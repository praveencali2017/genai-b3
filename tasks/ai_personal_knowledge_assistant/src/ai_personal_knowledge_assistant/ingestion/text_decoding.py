"""Decoding of plain-text uploads, shared by validation and extraction.

Both must agree on what counts as readable text, so the rule lives in one place.
"""


def decode_text(content: bytes) -> str:
    """Decode a text file's bytes: UTF-8 (a leading BOM is dropped), else Latin-1.

    Latin-1 maps every byte to a character, so this never fails; telling text
    from binary is the job of `looks_binary`, not of the decoder.
    """
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError:
        return content.decode("latin-1")


def looks_binary(content: bytes) -> bool:
    """True if the bytes can't be a UTF-8 / Latin-1 text file.

    A NUL byte never appears in such text but is common in binary formats (and
    in UTF-16, which isn't supported).
    """
    return b"\x00" in content
