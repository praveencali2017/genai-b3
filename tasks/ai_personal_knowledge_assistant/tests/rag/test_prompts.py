"""The grounded-answer prompt and the numbered context it is given."""

from ai_personal_knowledge_assistant.rag.prompts import (
    GROUNDED_ANSWER_INSTRUCTIONS,
    NOT_FOUND_ANSWER,
    build_context,
    build_question_message,
)
from ai_personal_knowledge_assistant.vectorstore.chroma_store import RetrievedChunk


def chunk(
    text: str, filename: str = "HR_Policy.pdf", page: int | None = 2
) -> RetrievedChunk:
    return RetrievedChunk(
        document_id=1,
        filename=filename,
        page=page,
        chunk_index=0,
        text=text,
        score=0.5,
    )


def test_context_numbers_excerpts_from_one_with_filename_and_page() -> None:
    context = build_context(
        [
            chunk("Casual leave: 12 days."),
            chunk("Sick leave: 10 days.", page=3),
        ]
    )

    assert context == (
        "[1] (HR_Policy.pdf, page 2) Casual leave: 12 days.\n\n"
        "[2] (HR_Policy.pdf, page 3) Sick leave: 10 days."
    )


def test_an_unpaginated_excerpt_is_labelled_with_the_filename_only() -> None:
    context = build_context([chunk("Taxis are reimbursed.", "Travel.txt", page=None)])

    assert context == "[1] (Travel.txt) Taxis are reimbursed."


def test_excerpt_text_is_trimmed() -> None:
    assert build_context([chunk("\n  Casual leave.  \n")]).endswith(") Casual leave.")


def test_no_excerpts_give_an_empty_context() -> None:
    assert build_context([]) == ""


def test_question_message_holds_the_context_then_the_question() -> None:
    message = build_question_message("How many days?", "[1] (a.pdf, page 1) Ten.")

    assert message.index("[1] (a.pdf, page 1) Ten.") < message.index(
        "Question: How many days?"
    )


def test_instructions_require_citations_and_the_exact_not_found_reply() -> None:
    assert NOT_FOUND_ANSWER in GROUNDED_ANSWER_INSTRUCTIONS
    assert "[n]" in GROUNDED_ANSWER_INSTRUCTIONS
    assert "outside knowledge" in GROUNDED_ANSWER_INSTRUCTIONS


def test_instructions_treat_excerpts_as_data() -> None:
    assert "not instructions" in GROUNDED_ANSWER_INSTRUCTIONS
