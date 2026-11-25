"""Streamlit UI: upload documents, ask questions about them, chat with the agent.

Run with `uv run pka-ui`. The UI holds no business logic: everything goes through
the HTTP API (`api_client`), at API_BASE_URL. Streamlit reruns this script on
every interaction, so all state that must survive a rerun (chat histories,
session ids, the last upload's outcome) lives in `st.session_state`.
"""

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import streamlit as st

from ai_personal_knowledge_assistant.core.settings import get_settings
from ai_personal_knowledge_assistant.frontend.api_client import (
    ApiError,
    KnowledgeApiClient,
)

Message = dict[str, Any]


@dataclass(frozen=True)
class ChatTab:
    """One chat mode: its own endpoint, history and API session."""

    kind: str
    label: str
    placeholder: str

    @property
    def history_key(self) -> str:
        return f"{self.kind}_messages"

    @property
    def session_key(self) -> str:
        return f"{self.kind}_session_id"


ASK_TAB = ChatTab("ask", "💬 Ask documents", "Ask a question about your documents")
AGENT_TAB = ChatTab("agent", "🤖 Agent chat", "Message the agent")


def main() -> None:
    st.set_page_config(page_title="AI Personal Knowledge Assistant", layout="wide")
    client = KnowledgeApiClient(get_settings().api_base_url)
    for tab in (ASK_TAB, AGENT_TAB):
        st.session_state.setdefault(tab.history_key, [])
        st.session_state.setdefault(tab.session_key, None)
    st.session_state.setdefault("uploader_generation", 0)

    with st.sidebar:
        render_sidebar(client)

    st.title("AI Personal Knowledge Assistant")
    st.caption(
        "Answers come only from your uploaded documents, with sources. "
        "The agent decides on its own which document tools to use."
    )
    ask_area, agent_area = st.tabs([ASK_TAB.label, AGENT_TAB.label])
    with ask_area:
        render_chat_tab(client, ASK_TAB)
    with agent_area:
        render_chat_tab(client, AGENT_TAB)


# --- Sidebar ---------------------------------------------------------------


def render_sidebar(client: KnowledgeApiClient) -> None:
    st.header("Knowledge base")
    render_api_status(client)
    st.divider()
    render_uploader(client)
    st.divider()
    render_document_list(client)


def render_api_status(client: KnowledgeApiClient) -> None:
    try:
        health = client.health()
    except ApiError as error:
        st.error(f"API unavailable: {error.message}")
        return
    st.success(
        f"API online · {health['ready_documents']} documents · "
        f"{health['stored_chunks']} chunks",
        icon="✅",
    )


def render_uploader(client: KnowledgeApiClient) -> None:
    st.subheader("Upload a document")
    notice = st.session_state.pop("upload_notice", None)
    if notice is not None:
        level, text = notice
        (st.success if level == "success" else st.error)(text)

    # A new key empties the uploader after a successful upload.
    uploaded = st.file_uploader(
        "PDF, DOCX or TXT",
        type=["pdf", "docx", "txt"],
        key=f"uploader_{st.session_state.uploader_generation}",
    )
    # The upload happens only on the run where the button was clicked, so
    # Streamlit's reruns never send the same file twice.
    if not st.button("Upload & index", disabled=uploaded is None, type="primary"):
        return
    with st.spinner(f"Indexing {uploaded.name}…"):
        try:
            document = client.upload(uploaded.name, uploaded.getvalue())
        except ApiError as error:
            st.error(f"Upload failed: {error.message}")
            return
    st.session_state.upload_notice = (
        "success",
        f"Indexed {document['filename']} ({document['chunk_count']} chunks).",
    )
    st.session_state.uploader_generation += 1
    st.rerun()


def render_document_list(client: KnowledgeApiClient) -> None:
    st.subheader("Documents")
    try:
        documents = client.list_documents()
    except ApiError as error:
        st.error(f"Couldn't load documents: {error.message}")
        return
    if not documents:
        st.info("No documents yet — upload one above.")
        return
    for document in documents:
        st.markdown(f"**{document['filename']}**")
        st.caption(_document_details(document))


def _document_details(document: dict[str, Any]) -> str:
    details = [document["file_type"].upper()]
    if document["page_count"] is not None:
        details.append(_plural(document["page_count"], "page"))
    details.append(_plural(document["chunk_count"], "chunk"))
    # The API sends UTC; show the viewer's local time.
    uploaded_at = datetime.fromisoformat(document["created_at"]).astimezone()
    details.append(f"uploaded {uploaded_at:%Y-%m-%d %H:%M}")
    return " · ".join(details)


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


# --- Chat tabs ---------------------------------------------------------------


def render_chat_tab(client: KnowledgeApiClient, tab: ChatTab) -> None:
    if st.button("New conversation", key=f"{tab.kind}_new_conversation"):
        st.session_state[tab.history_key] = []
        st.session_state[tab.session_key] = None

    history: list[Message] = st.session_state[tab.history_key]
    transcript = st.container()
    with transcript:
        if not history:
            st.caption("No messages yet.")
        for message in history:
            render_message(message)

    prompt = st.chat_input(tab.placeholder, key=f"{tab.kind}_chat_input")
    if not prompt:
        return
    user_message: Message = {"role": "user", "content": prompt}
    history.append(user_message)
    with transcript:
        render_message(user_message)
        with st.spinner("Thinking…"):
            reply = _send(client, tab, prompt)
        history.append(reply)
        render_message(reply)


def _send(client: KnowledgeApiClient, tab: ChatTab, prompt: str) -> Message:
    """Call the tab's endpoint and turn the response, or failure, into a message."""
    session_id = st.session_state[tab.session_key]
    try:
        if tab.kind == AGENT_TAB.kind:
            response = client.agent_chat(prompt, session_id)
        else:
            response = client.ask(prompt, session_id)
    except ApiError as error:
        return {"role": "assistant", "error": error.message}

    st.session_state[tab.session_key] = response["session_id"]
    return {
        "role": "assistant",
        "content": response.get("answer", response.get("reply", "")),
        "is_grounded": response.get("is_grounded"),
        "sources": response.get("sources", []),
        "tool_calls": response.get("tool_calls"),
    }


def render_message(message: Message) -> None:
    with st.chat_message(message["role"]):
        if "error" in message:
            st.error(message["error"])
            return
        st.markdown(message["content"])
        if message.get("tool_calls") is not None:
            render_tool_calls(message["tool_calls"])
        if message.get("is_grounded") is False:
            st.caption("Not found in your documents")
        if message.get("sources"):
            render_sources(message["sources"])


def render_tool_calls(tool_calls: list[dict[str, Any]]) -> None:
    if not tool_calls:
        st.caption("Tools used: none")
        return
    lines = ["**Tools used**"]
    for call in tool_calls:
        icon = "⚠️" if call["is_error"] else "🔧"
        lines.append(
            f"{icon} `{call['tool']}`{_format_arguments(call['arguments'])} "
            f"— {call['summary']}"
        )
    st.caption("  \n".join(lines))


def _format_arguments(arguments: Any) -> str:
    if not arguments:
        return ""
    # The API passes invalid JSON from the model through as raw text.
    if isinstance(arguments, str):
        return f" `{arguments}`"
    return f" `{json.dumps(arguments, ensure_ascii=False)}`"


def render_sources(sources: list[dict[str, Any]]) -> None:
    with st.expander(f"Sources ({len(sources)})"):
        for source in sources:
            location = source["filename"]
            if source["page"] is not None:
                location += f" — page {source['page']}"
            st.markdown(
                f"**[{source['citation']}] {location}** (score {source['score']:.2f})"
            )
            st.caption(source["snippet"])


main()
