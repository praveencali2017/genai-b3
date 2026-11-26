from ai_travel_agent.rag.vector_store import ChunkHit
from ai_travel_agent.services.chat_service import _citations


def _hit(page: int | None, index: int, filename: str = "Policy.pdf") -> ChunkHit:
    return ChunkHit(1, filename, 1, page, index, "text", 0.9)


EVIDENCE = [_hit(1, 0), _hit(2, 1), _hit(10, 2), _hit(None, 0, "FAQ.txt")]


def test_only_the_pages_the_answer_names_are_cited() -> None:
    cited = _citations(EVIDENCE, "See [Policy.pdf p.10] and [FAQ.txt].")

    # "p.1" must not match inside "p.10".
    assert [(c.filename, c.page) for c in cited] == [
        ("Policy.pdf", 10),
        ("FAQ.txt", None),
    ]


def test_naming_a_document_without_a_page_cites_every_retrieved_page() -> None:
    assert [c.page for c in _citations(EVIDENCE, "As Policy.pdf says...")] == [1, 2, 10]


def test_an_answer_without_sources_gets_no_citation_chips() -> None:
    assert _citations(EVIDENCE, "I can't verify that policy.") == []


def test_a_page_list_after_one_filename_cites_each_page() -> None:
    cited = _citations(EVIDENCE, "Refund rules [Policy.pdf p.1, p.10].")

    assert [c.page for c in cited] == [1, 10]
