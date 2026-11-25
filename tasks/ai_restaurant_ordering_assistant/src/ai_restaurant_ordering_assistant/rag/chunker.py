"""Split page text into overlapping chunks for embedding.

Pure and deterministic (ported from the personal knowledge assistant). Per page:

1. Cut the text into *units* no longer than `chunk_size`, at the coarsest
   boundary that works: paragraphs, then sentences, then lines, then words. A
   word longer than `chunk_size` is hard-split, so any input terminates.
2. Pack consecutive units greedily into chunks of at most `chunk_size`
   characters. Each new chunk starts with the trailing units of the previous
   one that fit within `overlap` characters, so context carries across a cut.

Chunks never span pages, which keeps each chunk's page number exact.
"""

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from itertools import pairwise

from ai_restaurant_ordering_assistant.rag.loader import Page

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
    if not 0 <= overlap < chunk_size:
        raise ValueError(f"need 0 <= overlap ({overlap}) < chunk_size ({chunk_size})")
    chunks: list[Chunk] = []
    for page in pages:
        for text in _chunk_texts(page.text, chunk_size, overlap):
            chunks.append(Chunk(index=len(chunks), text=text, page=page.number))
    return chunks


def _chunk_texts(text: str, chunk_size: int, overlap: int) -> list[str]:
    units = _split(text, (0, len(text)), chunk_size, level=0)
    slices = (
        text[start:end].strip() for start, end in _pack(units, chunk_size, overlap)
    )
    return [piece for piece in slices if piece]


def _split(text: str, span: Span, chunk_size: int, level: int) -> list[Span]:
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
    return [
        unit
        for piece in pairwise([start, *cuts, end])
        for unit in _split(text, piece, chunk_size, level + 1)
    ]


def _pack(units: Sequence[Span], chunk_size: int, overlap: int) -> list[Span]:
    chunk_spans: list[Span] = []
    window: list[Span] = []
    for unit in units:
        if window and unit[1] - window[0][0] > chunk_size:
            chunk_spans.append((window[0][0], window[-1][1]))
            end = window[-1][1]
            window = [u for u in window if end - u[0] <= overlap]
            while window and unit[1] - window[0][0] > chunk_size:
                window.pop(0)
        window.append(unit)
    if window:
        chunk_spans.append((window[0][0], window[-1][1]))
    return chunk_spans
