"""Captions and labels the UI renders for documents, tool calls and sources."""

import time
from collections.abc import Iterator
from typing import Any

import pytest

from ai_personal_knowledge_assistant.frontend.formatting import (
    document_details,
    format_arguments,
    plural,
    source_heading,
    tool_calls_caption,
)


@pytest.fixture
def india_time(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Run in UTC+05:30 (no daylight saving), so local-time output is fixed."""
    monkeypatch.setenv("TZ", "Asia/Kolkata")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def document(**overrides: Any) -> dict[str, Any]:
    return {
        "file_type": "pdf",
        "page_count": 3,
        "chunk_count": 4,
        "created_at": "2026-11-24T09:00:00+00:00",
        **overrides,
    }


# --- Documents ---


@pytest.mark.usefixtures("india_time")
def test_document_details_show_type_counts_and_local_upload_time() -> None:
    assert document_details(document()) == (
        "PDF · 3 pages · 4 chunks · uploaded 2026-11-24 14:30"
    )


@pytest.mark.usefixtures("india_time")
def test_documents_without_pages_omit_the_page_count() -> None:
    details = document_details(
        document(file_type="txt", page_count=None, chunk_count=1)
    )

    assert details == "TXT · 1 chunk · uploaded 2026-11-24 14:30"


@pytest.mark.usefixtures("india_time")
def test_the_upload_time_crosses_midnight_into_the_local_date() -> None:
    details = document_details(document(created_at="2026-11-24T20:15:00+00:00"))

    assert details.endswith("uploaded 2026-11-25 01:45")


@pytest.mark.parametrize(
    ("count", "expected"),
    [(0, "0 pages"), (1, "1 page"), (2, "2 pages")],
)
def test_plural(count: int, expected: str) -> None:
    assert plural(count, "page") == expected


# --- Tool calls ---


def test_no_tool_calls_say_none() -> None:
    assert tool_calls_caption([]) == "Tools used: none"


def test_each_tool_call_gets_a_line_with_its_arguments_and_summary() -> None:
    caption = tool_calls_caption(
        [
            {
                "tool": "list_documents",
                "arguments": {},
                "is_error": False,
                "summary": "1 document",
            },
            {
                "tool": "search_documents",
                "arguments": {"query": "congé"},
                "is_error": False,
                "summary": "2 excerpts",
            },
            {
                "tool": "delete_documents",
                "arguments": "{not json",
                "is_error": True,
                "summary": "Unknown tool 'delete_documents'.",
            },
        ]
    )

    assert caption.split("  \n") == [
        "**Tools used**",
        "🔧 `list_documents` — 1 document",
        '🔧 `search_documents` `{"query": "congé"}` — 2 excerpts',
        "⚠️ `delete_documents` `{not json` — Unknown tool 'delete_documents'.",
    ]


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        (None, ""),
        ({}, ""),
        ("", ""),
        ({"top_k": 3}, ' `{"top_k": 3}`'),
        ('{"query": ', ' `{"query": `'),
    ],
    ids=["none", "empty-dict", "empty-string", "dict", "raw-text"],
)
def test_format_arguments(arguments: Any, expected: str) -> None:
    assert format_arguments(arguments) == expected


# --- Sources ---


def test_a_source_heading_has_citation_file_page_and_score() -> None:
    source = {"citation": 1, "filename": "HR_Policy.pdf", "page": 2, "score": 0.8149}

    assert source_heading(source) == "**[1] HR_Policy.pdf — page 2** (score 0.81)"


def test_a_source_without_a_page_shows_only_the_file() -> None:
    source = {
        "citation": 2,
        "filename": "Travel_Policy.txt",
        "page": None,
        "score": 0.5,
    }

    assert source_heading(source) == "**[2] Travel_Policy.txt** (score 0.50)"
