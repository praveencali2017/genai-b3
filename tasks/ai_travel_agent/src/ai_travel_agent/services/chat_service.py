"""Chat sessions and turns: persist, run the agent, persist the reply.

A turn uses three short transactions instead of one long one, so no database
lock is held while the model thinks:

1. Lock the session row, store the user's message, commit. The message is
   kept even if everything after it fails.
2. Run the agent (no transaction). Proposals it makes are committed by
   `propose` as they happen.
3. Lock the session row again and store the reply with its mode, citations,
   pending action ids and tool-run audit records.

If the agent fails, step 3 stores an assistant message that records the error,
and the error is re-raised for the HTTP response (503 for an AI outage).
"""

import logging
import re
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from functools import partial
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from ai_travel_agent.agents.api_client import TravelApiClient
from ai_travel_agent.agents.prompts import system_prompt
from ai_travel_agent.agents.single_agent import (
    AgentReply,
    HistoryTurn,
    SingleAgent,
)
from ai_travel_agent.agents.tools import (
    AgentToolkit,
    ProposedAction,
)
from ai_travel_agent.config.database import session_scope
from ai_travel_agent.exceptions.errors import (
    AssistantNotConfiguredError,
    ResourceNotFoundError,
    TravelError,
)
from ai_travel_agent.models import ChatSession, PendingAction, ToolRun
from ai_travel_agent.models.base import utc_now
from ai_travel_agent.models.enums import (
    ChatMode,
    MessageRole,
    PendingActionType,
    ToolRunStatus,
)
from ai_travel_agent.models.user import User
from ai_travel_agent.rag.retriever import PolicyRetriever
from ai_travel_agent.rag.vector_store import ChunkHit
from ai_travel_agent.repositories.chat_repository import (
    ChatRepository,
)
from ai_travel_agent.repositories.pending_action_repository import (
    PendingActionRepository,
)
from ai_travel_agent.schemas.chat import (
    ChatMessageResponse,
    ChatTurnResponse,
    Citation,
)
from ai_travel_agent.services.rate_limiter import (
    SlidingWindowRateLimiter,
)

CHAT_SESSION = "Chat session"
DEFAULT_TITLE = "New chat"
TITLE_LENGTH = 60
FAILED_REPLY = "Sorry, I couldn't answer that just now. Please try again."

logger = logging.getLogger(__name__)

ApiClientFactory = Callable[[int], TravelApiClient]


@dataclass(frozen=True, slots=True)
class ChatConfig:
    context_turns: int
    pending_action_ttl: timedelta


class ChatService:
    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        agent: SingleAgent | None,
        retriever: PolicyRetriever,
        api_client_factory: ApiClientFactory,
        rate_limiter: SlidingWindowRateLimiter,
        config: ChatConfig,
    ) -> None:
        self._session_factory = session_factory
        self._agent = agent
        self._retriever = retriever
        self._api_client_factory = api_client_factory
        self._rate_limiter = rate_limiter
        self._config = config

    # ------------------------------------------------------------ sessions

    def create_session(self, user: User) -> ChatSession:
        with session_scope(self._session_factory) as session:
            return ChatRepository(session).add_session(
                ChatSession(user_id=user.id, title=DEFAULT_TITLE)
            )

    def list_sessions(self, user: User) -> Sequence[ChatSession]:
        with session_scope(self._session_factory) as session:
            return ChatRepository(session).list_sessions(user.id)

    def list_messages(self, user: User, session_id: int) -> list[ChatMessageResponse]:
        with session_scope(self._session_factory) as session:
            repository = ChatRepository(session)
            _owned(repository.get_session(session_id), user, session_id)
            actions = _actions_by_id(session, session_id)
            return [
                ChatMessageResponse.from_message(message, actions)
                for message in repository.list_messages(session_id, limit=500)
            ]

    # ------------------------------------------------------------ turns

    def send_message(
        self, user: User, session_id: int, content: str
    ) -> ChatTurnResponse:
        self._rate_limiter.check(user.id)

        with session_scope(self._session_factory) as session:
            repository = ChatRepository(session)
            chat = _owned(repository.lock_session(session_id), user, session_id)
            history = [
                HistoryTurn(message.role, message.content)
                for message in repository.recent_messages(
                    session_id, self._config.context_turns
                )
                if not message.message_metadata.get("error")
            ]
            user_message = repository.append_message(chat, MessageRole.USER, content)
            if chat.title == DEFAULT_TITLE:
                chat.title = _title_from(content)
            user_response = ChatMessageResponse.from_message(user_message, {})

        try:
            reply = self._run_agent(user, session_id, history, content)
        except Exception as error:
            self._store_failure(session_id, error)
            raise

        with session_scope(self._session_factory) as session:
            repository = ChatRepository(session)
            chat = _owned(repository.lock_session(session_id), user, session_id)
            message = repository.append_message(
                chat,
                MessageRole.ASSISTANT,
                reply.content,
                metadata={
                    "mode": reply.mode,
                    "citations": [
                        c.model_dump()
                        for c in _citations(reply.evidence, reply.content)
                    ],
                    "pending_action_ids": reply.pending_action_ids,
                },
            )
            repository.add_tool_runs(message, _tool_runs(reply))
            actions = _actions_by_id(session, session_id)
            return ChatTurnResponse(
                user_message=user_response,
                assistant_message=ChatMessageResponse.from_message(message, actions),
            )

    def _run_agent(
        self, user: User, session_id: int, history: list[HistoryTurn], content: str
    ) -> AgentReply:
        if self._agent is None:
            raise AssistantNotConfiguredError(
                "The chatbot needs a language model: set OPENAI_API_KEY (or "
                "OPENAI_BASE_URL for another provider) in .env and restart."
            )
        api = self._api_client_factory(user.id)
        try:
            toolkit = AgentToolkit(
                api=api,
                retriever=self._retriever,
                propose=partial(self._propose, user.id, session_id),
            )
            return self._agent.reply(
                system_prompt(user.username, date.today()), history, content, toolkit
            )
        finally:
            api.close()

    def _propose(
        self,
        user_id: int,
        session_id: int,
        action_type: PendingActionType,
        terms: dict[str, Any],
    ) -> ProposedAction:
        with session_scope(self._session_factory) as session:
            action = PendingActionRepository(session).add(
                PendingAction(
                    session_id=session_id,
                    user_id=user_id,
                    action=action_type,
                    payload=terms,
                    expires_at=utc_now() + self._config.pending_action_ttl,
                    idempotency_key=f"chat-{uuid.uuid4().hex}",
                )
            )
            return ProposedAction(action.id, action.expires_at)

    def _store_failure(self, session_id: int, error: Exception) -> None:
        if isinstance(error, TravelError):
            code, detail = error.code, str(error)
        else:
            logger.exception("Chat turn in session %s failed", session_id)
            code, detail = "INTERNAL_ERROR", "An unexpected error occurred."
        with session_scope(self._session_factory) as session:
            repository = ChatRepository(session)
            chat = repository.lock_session(session_id)
            if chat is None:
                return
            repository.append_message(
                chat,
                MessageRole.ASSISTANT,
                FAILED_REPLY,
                metadata={
                    "mode": ChatMode.GENERAL,
                    "error": {"code": code, "message": detail},
                },
            )


def _owned(chat: ChatSession | None, user: User, session_id: int) -> ChatSession:
    # Another user's session is reported as missing, never as forbidden.
    if chat is None or chat.user_id != user.id:
        raise ResourceNotFoundError(CHAT_SESSION, session_id)
    return chat


def _actions_by_id(session: Session, session_id: int) -> dict[int, PendingAction]:
    return {
        action.id: action
        for action in PendingActionRepository(session).list_for_session(session_id)
    }


def _title_from(content: str) -> str:
    title = " ".join(content.split())
    if len(title) <= TITLE_LENGTH:
        return title
    return title[: TITLE_LENGTH - 1].rstrip() + "…"


def _citations(evidence: Sequence[ChunkHit], answer: str) -> list[Citation]:
    """One citation per document page the answer cites, in order of retrieval.

    Retrieved chunks the answer doesn't cite (e.g. when it says the policy
    can't be verified) are left out, so a source chip always backs a claim.
    """
    seen: dict[tuple[int, int | None], Citation] = {}
    for hit in evidence:
        if not _is_cited(hit, answer):
            continue
        seen.setdefault(
            (hit.document_id, hit.page),
            Citation(
                document_id=hit.document_id,
                filename=hit.filename,
                version=hit.version,
                page=hit.page,
                chunk_id=hit.chunk_id,
            ),
        )
    return list(seen.values())


def _is_cited(hit: ChunkHit, answer: str) -> bool:
    """True if the answer names this hit's page ("Travel_Policy.pdf p.2", also
    in a list such as "Travel_Policy.pdf p.1, p.2"), or names its document
    without any page at all."""
    if hit.filename not in answer:
        return False
    pages = _pages_cited(hit.filename, answer)
    return not pages or hit.page in pages


def _pages_cited(filename: str, answer: str) -> set[int]:
    page_lists = re.findall(re.escape(filename) + r"((?:[\s,;]*p\.\s*\d+)+)", answer)
    return {int(number) for pages in page_lists for number in re.findall(r"\d+", pages)}


def _tool_runs(reply: AgentReply) -> list[ToolRun]:
    return [
        ToolRun(
            tool_name=call.name,
            args=call.arguments,
            result=call.result,
            status=ToolRunStatus.ERROR if call.is_error else ToolRunStatus.SUCCESS,
        )
        for call in reply.tool_calls
    ]
