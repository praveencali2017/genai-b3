"""Text the UI shows for API objects: pure functions, no Streamlit calls.

They live apart from `app.py`, which runs the whole UI when imported, so they
can be unit tested.
"""

import json
from datetime import datetime
from typing import Any


def document_details(document: dict[str, Any]) -> str:
    """One caption line, e.g. "PDF · 3 pages · 4 chunks · uploaded 2026-11-24 09:30"."""
    details = [document["file_type"].upper()]
    if document["page_count"] is not None:
        details.append(plural(document["page_count"], "page"))
    details.append(plural(document["chunk_count"], "chunk"))
    # The API sends UTC; show the viewer's local time.
    uploaded_at = datetime.fromisoformat(document["created_at"]).astimezone()
    details.append(f"uploaded {uploaded_at:%Y-%m-%d %H:%M}")
    return " · ".join(details)


def plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def tool_calls_caption(tool_calls: list[dict[str, Any]]) -> str:
    """The "Tools used" caption under an agent reply, one line per call."""
    if not tool_calls:
        return "Tools used: none"
    lines = ["**Tools used**"]
    for call in tool_calls:
        icon = "⚠️" if call["is_error"] else "🔧"
        lines.append(
            f"{icon} `{call['tool']}`{format_arguments(call['arguments'])} "
            f"— {call['summary']}"
        )
    return "  \n".join(lines)


def format_arguments(arguments: Any) -> str:
    if not arguments:
        return ""
    # The API passes invalid JSON from the model through as raw text.
    if isinstance(arguments, str):
        return f" `{arguments}`"
    return f" `{json.dumps(arguments, ensure_ascii=False)}`"


def source_heading(source: dict[str, Any]) -> str:
    """E.g. "**[1] HR_Policy.pdf — page 2** (score 0.81)"."""
    location = source["filename"]
    if source["page"] is not None:
        location += f" — page {source['page']}"
    return f"**[{source['citation']}] {location}** (score {source['score']:.2f})"
