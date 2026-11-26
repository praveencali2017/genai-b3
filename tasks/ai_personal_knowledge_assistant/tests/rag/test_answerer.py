"""GroundedAnswerer: retrieval gate, model call, refusals and citations."""

import httpx2
import openai
import pytest

from ai_personal_knowledge_assistant.core.errors import (
    AssistantNotConfiguredError,
    AssistantUnavailableError,
)
from ai_personal_knowledge_assistant.ingestion.chunker import Chunk
from ai_personal_knowledge_assistant.rag.answerer import (
    SNIPPET_MAX_CHARS,
    ChatTurn,
    cited_numbers,
    is_not_found_reply,
)
from ai_personal_knowledge_assistant.rag.prompts import (
    GROUNDED_ANSWER_INSTRUCTIONS,
    NOT_FOUND_ANSWER,
)
from ai_personal_knowledge_assistant.storage.records import DocumentRecord, MessageRole
from ai_personal_knowledge_assistant.vectorstore.chroma_store import ChromaVectorStore
from tests.fakes import FakeOpenAI
from tests.rag.conftest import TEST_MODEL, make_answerer

CASUAL_LEAVE_QUESTION = "How many casual leaves are allowed?"
UNRELATED_QUESTION = "What is the capital of France?"


def question_message(fake_openai: FakeOpenAI, call: int = 0) -> str:
    return fake_openai.responses.calls[call]["input"][-1]["content"]


# --- Grounded answer from the real HR policy ---


def test_a_covered_question_is_answered_from_numbered_context(
    vector_store: ChromaVectorStore, hr_policy: DocumentRecord, fake_openai: FakeOpenAI
) -> None:
    fake_openai.responses.script("Employees get 12 days of casual leave a year [1].")

    answer = make_answerer(vector_store, fake_openai).answer(CASUAL_LEAVE_QUESTION)

    (call,) = fake_openai.responses.calls
    assert call["model"] == TEST_MODEL
    assert call["instructions"] == GROUNDED_ANSWER_INSTRUCTIONS
    assert call["input"][-1]["role"] == "user"
    message = question_message(fake_openai)
    assert "[1] (HR_Policy.pdf, page 2)" in message
    assert "12 days of casual leave" in message
    assert message.endswith(f"Question: {CASUAL_LEAVE_QUESTION}")

    assert answer.is_grounded
    assert answer.text == "Employees get 12 days of casual leave a year [1]."
    (source,) = answer.sources
    assert source.citation == 1
    assert source.document_id == hr_policy.id
    assert source.filename == "HR_Policy.pdf"
    assert source.page == 2
    assert "12 days of casual leave" in source.snippet
    assert 0 < source.score <= 1


def test_the_question_is_stripped_before_use(
    vector_store: ChromaVectorStore, hr_policy: DocumentRecord, fake_openai: FakeOpenAI
) -> None:
    fake_openai.responses.script("12 days [1].")

    make_answerer(vector_store, fake_openai).answer(f"  {CASUAL_LEAVE_QUESTION}\n")

    assert question_message(fake_openai).endswith(f"Question: {CASUAL_LEAVE_QUESTION}")


# --- Not found ---


def test_an_unrelated_question_is_not_found_without_calling_the_model(
    vector_store: ChromaVectorStore, hr_policy: DocumentRecord, fake_openai: FakeOpenAI
) -> None:
    answer = make_answerer(vector_store, fake_openai).answer(UNRELATED_QUESTION)

    assert answer.text == NOT_FOUND_ANSWER
    assert not answer.is_grounded
    assert answer.sources == []
    assert fake_openai.responses.calls == []


def test_not_found_needs_no_api_key(
    vector_store: ChromaVectorStore, hr_policy: DocumentRecord
) -> None:
    answer = make_answerer(vector_store, None).answer(UNRELATED_QUESTION)

    assert answer.text == NOT_FOUND_ANSWER
    assert not answer.is_grounded


def test_an_empty_library_is_not_found(
    vector_store: ChromaVectorStore, fake_openai: FakeOpenAI
) -> None:
    answer = make_answerer(vector_store, fake_openai).answer(CASUAL_LEAVE_QUESTION)

    assert answer.text == NOT_FOUND_ANSWER
    assert fake_openai.responses.calls == []


@pytest.mark.parametrize(
    "reply",
    [
        NOT_FOUND_ANSWER,
        "  i couldn’t find this in your uploaded documents  ",
        f'"{NOT_FOUND_ANSWER}"',
        f"**{NOT_FOUND_ANSWER}**",
        f"{NOT_FOUND_ANSWER} [1]",
        f"{NOT_FOUND_ANSWER} Generally, companies offer about 10 days.",
    ],
)
def test_a_not_found_reply_despite_hits_is_ungrounded(
    vector_store: ChromaVectorStore,
    hr_policy: DocumentRecord,
    fake_openai: FakeOpenAI,
    reply: str,
) -> None:
    fake_openai.responses.script(reply)

    answer = make_answerer(vector_store, fake_openai).answer(CASUAL_LEAVE_QUESTION)

    assert answer.text == NOT_FOUND_ANSWER
    assert not answer.is_grounded
    assert answer.sources == []


def test_a_reply_that_opens_with_not_found_but_cites_is_an_answer(
    vector_store: ChromaVectorStore, hr_policy: DocumentRecord, fake_openai: FakeOpenAI
) -> None:
    reply = f"{NOT_FOUND_ANSWER} Only casual leave is covered: 12 days [1]."
    fake_openai.responses.script(reply)

    answer = make_answerer(vector_store, fake_openai).answer(CASUAL_LEAVE_QUESTION)

    assert answer.is_grounded
    assert answer.text == reply
    assert [source.citation for source in answer.sources] == [1]


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        (NOT_FOUND_ANSWER, True),
        ("I COULDN'T FIND THIS IN YOUR UPLOADED DOCUMENTS!", True),
        ("Employees get 12 days [1].", False),
        (f"{NOT_FOUND_ANSWER} But here is a guess.", False),
        ("", False),
    ],
)
def test_is_not_found_reply(reply: str, expected: bool) -> None:
    assert is_not_found_reply(reply) is expected


# --- Model availability ---


def test_a_covered_question_without_a_client_is_not_configured(
    vector_store: ChromaVectorStore, hr_policy: DocumentRecord
) -> None:
    with pytest.raises(AssistantNotConfiguredError, match="OPENAI_API_KEY"):
        make_answerer(vector_store, None).answer(CASUAL_LEAVE_QUESTION)


def test_an_openai_failure_is_unavailable(
    vector_store: ChromaVectorStore, hr_policy: DocumentRecord, fake_openai: FakeOpenAI
) -> None:
    request = httpx2.Request("POST", "https://api.openai.com/v1/responses")
    fake_openai.responses.script(openai.APIConnectionError(request=request))

    with pytest.raises(AssistantUnavailableError):
        make_answerer(vector_store, fake_openai).answer(CASUAL_LEAVE_QUESTION)


def test_a_blank_question_is_rejected(
    vector_store: ChromaVectorStore, fake_openai: FakeOpenAI
) -> None:
    with pytest.raises(ValueError, match="blank"):
        make_answerer(vector_store, fake_openai).answer("   ")


# --- Citations ---

LEAVE_CHUNKS = [
    Chunk(index=0, text="Casual leave is twelve days.", page=2),
    Chunk(index=1, text="Casual leave cannot be carried forward.", page=2),
    Chunk(index=2, text="Casual leave needs one day notice.", page=2),
]


@pytest.fixture
def leave_store(vector_store: ChromaVectorStore) -> ChromaVectorStore:
    vector_store.add_chunks(1, "HR_Policy.pdf", LEAVE_CHUNKS)
    return vector_store


@pytest.mark.parametrize(
    ("reply", "cited"),
    [
        ("Twelve days [1][3].", [1, 3]),
        ("Twelve days [3, 1].", [1, 3]),
        ("Twelve days [2] and not carried forward [2].", [2]),
        ("Twelve days [2][9].", [2]),
        ("Twelve days [9].", [1, 2, 3]),
        ("Twelve days.", [1, 2, 3]),
    ],
)
def test_sources_are_the_cited_excerpts_or_all_when_none_is_cited(
    leave_store: ChromaVectorStore,
    fake_openai: FakeOpenAI,
    reply: str,
    cited: list[int],
) -> None:
    question = "casual leave"
    ranked = leave_store.query(question, top_k=3)
    fake_openai.responses.script(reply)

    answer = make_answerer(leave_store, fake_openai, min_relevance=0.0).answer(question)

    assert answer.is_grounded
    assert [source.citation for source in answer.sources] == cited
    assert [source.chunk_index for source in answer.sources] == [
        ranked[number - 1].chunk_index for number in cited
    ]


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ("A [1] B [3]", [1, 3]),
        ("A [1][3][1]", [1, 3]),
        ("A [1, 2 ,3]", [1, 2, 3]),
        ("A [0] [4] [99]", []),
        ("A [x] [1a] (1)", []),
        ("", []),
    ],
)
def test_cited_numbers(reply: str, expected: list[int]) -> None:
    assert cited_numbers(reply, excerpt_count=3) == expected


def test_snippets_are_short_and_whitespace_collapsed(
    vector_store: ChromaVectorStore, fake_openai: FakeOpenAI
) -> None:
    long_text = "Casual  leave\nrules. " + "word " * 200
    vector_store.add_chunks(
        1,
        "a.pdf",
        [
            Chunk(index=0, text=long_text, page=1),
            Chunk(index=1, text="Casual\n\nleave.", page=1),
        ],
    )
    fake_openai.responses.script("Rules [1][2].")

    answer = make_answerer(vector_store, fake_openai, min_relevance=0.0).answer(
        "casual leave"
    )

    snippets = {source.chunk_index: source.snippet for source in answer.sources}
    assert snippets[1] == "Casual leave."
    assert snippets[0].startswith("Casual leave rules. word")
    assert snippets[0].endswith("…")
    assert len(snippets[0]) <= SNIPPET_MAX_CHARS


# --- Document filter ---


def test_document_ids_limit_the_excerpts(
    vector_store: ChromaVectorStore, fake_openai: FakeOpenAI
) -> None:
    vector_store.add_chunks(1, "HR_Policy.pdf", [Chunk(0, "Casual leave: 12 days.", 2)])
    vector_store.add_chunks(
        2, "Travel.txt", [Chunk(0, "Casual dress when travelling.", None)]
    )
    fake_openai.responses.script("Dress casually [1].")

    answer = make_answerer(vector_store, fake_openai, min_relevance=0.0).answer(
        "casual", document_ids=[2]
    )

    assert "(Travel.txt)" in question_message(fake_openai)
    assert "HR_Policy.pdf" not in question_message(fake_openai)
    assert [source.document_id for source in answer.sources] == [2]


def test_an_empty_document_filter_is_not_found_without_the_model(
    vector_store: ChromaVectorStore, hr_policy: DocumentRecord, fake_openai: FakeOpenAI
) -> None:
    answer = make_answerer(vector_store, fake_openai).answer(
        CASUAL_LEAVE_QUESTION, document_ids=[]
    )

    assert not answer.is_grounded
    assert fake_openai.responses.calls == []


# --- History ---


def test_history_precedes_the_question_in_the_model_input(
    vector_store: ChromaVectorStore, hr_policy: DocumentRecord, fake_openai: FakeOpenAI
) -> None:
    history = [
        ChatTurn(MessageRole.USER, "Tell me about leave."),
        ChatTurn(MessageRole.ASSISTANT, "There is casual, sick and earned leave [1]."),
    ]
    fake_openai.responses.script("12 days [1].")

    make_answerer(vector_store, fake_openai).answer(
        CASUAL_LEAVE_QUESTION, history=history
    )

    model_input = fake_openai.responses.calls[0]["input"]
    assert model_input[:2] == [
        {"role": "user", "content": "Tell me about leave."},
        {"role": "assistant", "content": "There is casual, sick and earned leave [1]."},
    ]
    assert model_input[2]["role"] == "user"
    assert "Question: " + CASUAL_LEAVE_QUESTION in model_input[2]["content"]


def test_retrieval_uses_only_the_current_question(
    vector_store: ChromaVectorStore, hr_policy: DocumentRecord, fake_openai: FakeOpenAI
) -> None:
    history = [ChatTurn(MessageRole.USER, "How many casual leave days are there?")]

    answer = make_answerer(vector_store, fake_openai).answer(
        UNRELATED_QUESTION, history=history
    )

    assert not answer.is_grounded
    assert fake_openai.responses.calls == []
