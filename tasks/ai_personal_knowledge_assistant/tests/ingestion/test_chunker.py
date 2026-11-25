import re
from pathlib import Path

import pytest

from ai_personal_knowledge_assistant.ingestion.chunker import Chunk, chunk_pages
from ai_personal_knowledge_assistant.ingestion.extractors import Page, extract_pages
from ai_personal_knowledge_assistant.storage.records import FileType

PROSE = " ".join(
    f"Sentence number {n} talks about leave rules and travel limits." for n in range(40)
)


def words(text: str) -> list[str]:
    return text.split()


def test_short_page_is_a_single_chunk() -> None:
    assert chunk_pages([Page(4, "  Short text.  ")], chunk_size=100, overlap=10) == [
        Chunk(index=0, text="Short text.", page=4)
    ]


@pytest.mark.parametrize(("chunk_size", "overlap"), [(50, 0), (120, 30), (300, 100)])
def test_no_chunk_exceeds_the_size(chunk_size: int, overlap: int) -> None:
    chunks = chunk_pages([Page(1, PROSE)], chunk_size, overlap)

    assert len(chunks) > 1
    assert all(len(chunk.text) <= chunk_size for chunk in chunks)


def test_chunks_cover_all_the_text_in_order() -> None:
    chunks = chunk_pages([Page(1, PROSE)], chunk_size=120, overlap=0)

    assert words(" ".join(chunk.text for chunk in chunks)) == words(PROSE)


def test_consecutive_chunks_overlap() -> None:
    chunks = chunk_pages([Page(1, PROSE)], chunk_size=200, overlap=80)

    for previous, current in zip(chunks, chunks[1:], strict=False):
        shared_start = current.text[:30]
        assert shared_start in previous.text
        # The shared part comes from the end of the previous chunk, not anywhere.
        assert previous.text.rindex(shared_start) >= len(previous.text) - 80


def test_overlap_never_exceeds_the_setting() -> None:
    chunks = chunk_pages([Page(1, PROSE)], chunk_size=200, overlap=80)

    for previous, current in zip(chunks, chunks[1:], strict=False):
        overlap = next(
            size
            for size in range(min(len(previous.text), len(current.text)), -1, -1)
            if previous.text.endswith(current.text[:size])
        )
        assert overlap <= 80


def test_zero_overlap_shares_nothing() -> None:
    chunks = chunk_pages([Page(1, PROSE)], chunk_size=120, overlap=0)

    total_words = sum(len(words(chunk.text)) for chunk in chunks)
    assert total_words == len(words(PROSE))


def test_never_cuts_inside_a_word_when_avoidable() -> None:
    chunks = chunk_pages([Page(1, PROSE)], chunk_size=73, overlap=20)

    source_words = set(words(PROSE))
    for chunk in chunks:
        assert set(words(chunk.text)) <= source_words


def test_prefers_paragraph_then_sentence_boundaries() -> None:
    first = "First paragraph has one sentence. And another one here."
    second = "Second paragraph is here. It also has two sentences."
    text = f"{first}\n\n{second}"

    by_paragraph = chunk_pages([Page(1, text)], chunk_size=len(first) + 5, overlap=0)
    assert [chunk.text for chunk in by_paragraph] == [first, second]

    by_sentence = chunk_pages([Page(1, first)], chunk_size=40, overlap=0)
    assert [chunk.text for chunk in by_sentence] == [
        "First paragraph has one sentence.",
        "And another one here.",
    ]


def test_every_chunk_carries_its_page_and_never_spans_pages() -> None:
    pages = [Page(1, "Alpha " * 30), Page(2, "Beta " * 30), Page(None, "Gamma " * 30)]

    chunks = chunk_pages(pages, chunk_size=50, overlap=10)

    for chunk in chunks:
        expected_word = {1: "Alpha", 2: "Beta", None: "Gamma"}[chunk.page]
        assert set(words(chunk.text)) == {expected_word}
    assert {chunk.page for chunk in chunks} == {1, 2, None}


def test_index_runs_continuously_across_pages() -> None:
    pages = [Page(1, PROSE), Page(2, PROSE), Page(5, "Last.")]

    chunks = chunk_pages(pages, chunk_size=150, overlap=30)

    assert [chunk.index for chunk in chunks] == list(range(len(chunks)))
    assert chunks[-1] == Chunk(index=len(chunks) - 1, text="Last.", page=5)


@pytest.mark.parametrize("pages", [[], [Page(1, "")], [Page(1, "  \n\n \t ")]])
def test_no_text_gives_no_chunks(pages: list[Page]) -> None:
    assert chunk_pages(pages, chunk_size=100, overlap=10) == []


def test_huge_single_word_is_hard_split() -> None:
    word = "x" * 250

    chunks = chunk_pages([Page(1, f"start {word} end")], chunk_size=100, overlap=20)

    assert all(0 < len(chunk.text) <= 100 for chunk in chunks)
    assert "".join(chunk.text for chunk in chunks).count("x") >= 250
    assert chunks[0].text.startswith("start")
    assert chunks[-1].text.endswith("end")


def test_whitespace_runs_never_produce_empty_chunks() -> None:
    text = "word " + " " * 500 + "\n\n" * 50 + " other"

    chunks = chunk_pages([Page(1, text)], chunk_size=20, overlap=5)

    assert [chunk.text for chunk in chunks] == ["word", "other"]


def test_sample_pdf_chunks_keep_casual_leave_on_page_2(sample_docs_dir: Path) -> None:
    pages = extract_pages(sample_docs_dir / "HR_Policy.pdf", FileType.PDF)

    chunks = chunk_pages(pages, chunk_size=800, overlap=150)

    casual = [chunk for chunk in chunks if re.search(r"12 days of casual", chunk.text)]
    assert casual and all(chunk.page == 2 for chunk in casual)


@pytest.mark.parametrize(
    ("chunk_size", "overlap"),
    [(100, 100), (100, 150), (0, 0), (-5, 0), (100, -1)],
)
def test_rejects_invalid_arguments(chunk_size: int, overlap: int) -> None:
    with pytest.raises(ValueError):
        chunk_pages([Page(1, "text")], chunk_size, overlap)
