from pathlib import Path

import pytest

from ai_travel_agent.exceptions.errors import UnsupportedFileError
from ai_travel_agent.rag.chunker import chunk_pages
from ai_travel_agent.rag.loader import Page, detect_file_type, extract_pages

KNOWLEDGE_BASE = Path(__file__).resolve().parents[2] / "knowledge_base"


@pytest.mark.parametrize(
    "filename",
    [
        "Cancellation_Policy.pdf",
        "Travel_Policy.pdf",
        "Destination_Guide.pdf",
        "Travel_FAQ.txt",
    ],
)
def test_starter_documents_are_versioned_and_chunk_within_limits(
    filename: str,
) -> None:
    data = (KNOWLEDGE_BASE / filename).read_bytes()
    pages = extract_pages(data, detect_file_type(filename, data))

    assert "Version 1.0 - Effective 2026-01-01" in pages[0].text
    chunks = chunk_pages(pages, chunk_size=800, overlap=100)
    assert chunks
    assert all(len(chunk.text) <= 800 for chunk in chunks)
    assert [chunk.index for chunk in chunks] == list(range(len(chunks)))


def test_pdf_chunks_keep_their_page_numbers() -> None:
    data = (KNOWLEDGE_BASE / "Cancellation_Policy.pdf").read_bytes()
    chunks = chunk_pages(extract_pages(data, detect_file_type("x.pdf", data)), 800, 100)

    assert {chunk.page for chunk in chunks} == {1, 2, 3}
    first_page = " ".join(chunk.text for chunk in chunks if chunk.page == 1)
    assert "at least 7 days before departure" in first_page
    assert "between 2 and 6 days" in first_page


def test_overlap_carries_context_across_a_cut() -> None:
    sentences = " ".join(f"Sentence number {n} is here." for n in range(60))
    chunks = chunk_pages([Page(number=None, text=sentences)], 200, 60)

    assert len(chunks) > 1
    for previous, current in zip(chunks, chunks[1:], strict=False):
        assert previous.text.split(". ")[-1].rstrip(".") in current.text


def test_a_file_whose_content_contradicts_its_extension_is_refused() -> None:
    with pytest.raises(UnsupportedFileError):
        detect_file_type("policy.pdf", b"just some plain text")
