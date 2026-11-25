"""Split page text into overlapping chunks for embedding.

Pure and deterministic. The approach, per page:

1. Cut the text into *units* no longer than `chunk_size`, using the coarsest
   boundary that works: paragraphs, then sentences, then lines, then words. A
   single word longer than `chunk_size` is hard-split, so any input terminates.
2. Pack consecutive units greedily into chunks of at most `chunk_size`
   characters. Each new chunk starts with the trailing units of the previous one
   that fit within `overlap` characters, so context carries across the cut.
   Overlap therefore follows unit boundaries and is "about" `overlap`, never
   more; it is zero only when the last unit alone is longer than `overlap`
   (e.g. after a hard split).

Chunks never span pages, which keeps each chunk's page number exact for
citations. Units are kept as offsets into the page text, so chunks are exact
slices of it (trimmed of surrounding whitespace).
"""

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from itertools import pairwise

from ai_personal_knowledge_assistant.ingestion.extractors import Page

# Coarsest first. Each cut falls at the end of the match, so the separator
# stays with the text before it and units cover the page with no gaps.
_BOUNDARIES: tuple[re.Pattern[str], ...] = (
    re.compile(r"\n\s*\n"),  # paragraph
    re.compile(r"(?<=[.!?])[\"')\]]*\s+"),  # sentence
    re.compile(r"\n"),  # line
    re.compile(r"\s+"),  # word
)

Span = tuple[int, int]


@dataclass(frozen=True, slots=True)
class Chunk:
    """A piece of one page. `index` runs across the whole document from 0."""

    index: int
    text: str
    page: int | None


def chunk_pages(pages: Iterable[Page], chunk_size: int, overlap: int) -> list[Chunk]:
    """Split pages into overlapping chunks of at most `chunk_size` characters."""
    _check_arguments(chunk_size, overlap)
    chunks: list[Chunk] = []
    for page in pages:
        for text in _chunk_texts(page.text, chunk_size, overlap):
            chunks.append(Chunk(index=len(chunks), text=text, page=page.number))
    return chunks


def _check_arguments(chunk_size: int, overlap: int) -> None:
    if chunk_size <= 0:
        raise ValueError(f"chunk_size must be positive, got {chunk_size}")
    if overlap < 0:
        raise ValueError(f"overlap must not be negative, got {overlap}")
    if overlap >= chunk_size:
        raise ValueError(
            f"overlap ({overlap}) must be smaller than chunk_size ({chunk_size})"
        )


def _chunk_texts(text: str, chunk_size: int, overlap: int) -> list[str]:
    units = _split(text, (0, len(text)), chunk_size, level=0)
    slices = (
        text[start:end].strip() for start, end in _pack(units, chunk_size, overlap)
    )
    return [piece for piece in slices if piece]


def _split(text: str, span: Span, chunk_size: int, level: int) -> list[Span]:
    """Cut `span` into contiguous units of at most `chunk_size` characters."""
    start, end = span
    if end - start <= chunk_size:
        return [span]
    if level == len(_BOUNDARIES):
        return [
            (cut, min(cut + chunk_size, end)) for cut in range(start, end, chunk_size)
        ]
    cuts = [
        match.end()
        for match in _BOUNDARIES[level].finditer(text, start, end)
        if start < match.end() < end
    ]
    edges = [start, *cuts, end]
    return [
        unit
        for piece in pairwise(edges)
        for unit in _split(text, piece, chunk_size, level + 1)
    ]


def _pack(units: Sequence[Span], chunk_size: int, overlap: int) -> list[Span]:
    """Group contiguous units into chunk spans, carrying an overlap tail forward."""
    chunk_spans: list[Span] = []
    window: list[Span] = []
    for unit in units:
        if window and unit[1] - window[0][0] > chunk_size:
            chunk_spans.append((window[0][0], window[-1][1]))
            window = _overlap_tail(window, overlap)
            # Drop tail units until the new unit fits; this always removes at
            # least one, since the full window plus this unit did not fit.
            while window and unit[1] - window[0][0] > chunk_size:
                window.pop(0)
        window.append(unit)
    if window:
        chunk_spans.append((window[0][0], window[-1][1]))
    return chunk_spans


def _overlap_tail(window: list[Span], overlap: int) -> list[Span]:
    """The trailing units of `window` that together span at most `overlap`."""
    end = window[-1][1]
    return [unit for unit in window if end - unit[0] <= overlap]
