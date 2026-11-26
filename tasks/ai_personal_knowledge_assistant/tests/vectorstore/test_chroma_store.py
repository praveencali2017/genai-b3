"""ChromaVectorStore against a real on-disk Chroma, with the fake embedder."""

from pathlib import Path

import chromadb
import pytest
from chromadb.api.client import SharedSystemClient
from chromadb.config import Settings as ChromaSettings

from ai_personal_knowledge_assistant.core.errors import VectorStoreError
from ai_personal_knowledge_assistant.ingestion.chunker import Chunk
from ai_personal_knowledge_assistant.vectorstore.chroma_store import (
    ChromaVectorStore,
    RetrievedChunk,
)
from tests.conftest import TEST_COLLECTION
from tests.fakes import FakeEmbedder

HR_CHUNKS = [
    Chunk(index=0, text="Casual leave: twelve days per calendar year.", page=2),
    Chunk(index=1, text="Sick leave needs a medical certificate.", page=2),
    Chunk(index=2, text="Office hours run nine to five, Monday to Friday.", page=3),
]
TRAVEL_CHUNKS = [
    Chunk(index=0, text="Book flights through the travel desk.", page=None),
    Chunk(index=1, text="Hotel stays are reimbursed up to a nightly cap.", page=None),
]


@pytest.fixture
def filled_store(vector_store: ChromaVectorStore) -> ChromaVectorStore:
    vector_store.add_chunks(1, "HR_Policy.pdf", HR_CHUNKS)
    vector_store.add_chunks(2, "Travel_Policy.txt", TRAVEL_CHUNKS)
    return vector_store


def test_query_returns_the_matching_chunk_with_its_metadata(
    filled_store: ChromaVectorStore,
) -> None:
    hits = filled_store.query("casual leave days", top_k=1)

    assert len(hits) == 1
    hit = hits[0]
    assert hit == RetrievedChunk(
        document_id=1,
        filename="HR_Policy.pdf",
        page=2,
        chunk_index=0,
        text=HR_CHUNKS[0].text,
        score=hit.score,
    )
    assert hit.score > 0.5


def test_unpaginated_chunks_come_back_with_page_none(
    filled_store: ChromaVectorStore,
) -> None:
    [hit] = filled_store.query("flights travel desk", top_k=1)

    assert (hit.filename, hit.page, hit.chunk_index) == ("Travel_Policy.txt", None, 0)


def test_scores_are_similarities_in_0_to_1_sorted_best_first(
    filled_store: ChromaVectorStore,
) -> None:
    hits = filled_store.query("leave", top_k=10)

    assert len(hits) == len(HR_CHUNKS) + len(TRAVEL_CHUNKS)
    scores = [hit.score for hit in hits]
    assert scores == sorted(scores, reverse=True)
    assert all(0.0 <= score <= 1.0 for score in scores)
    assert {hit.chunk_index for hit in hits[:2] if hit.document_id == 1} == {0, 1}
    assert scores[-1] == pytest.approx(0.0, abs=1e-6)


def test_identical_text_scores_one(filled_store: ChromaVectorStore) -> None:
    [hit] = filled_store.query(HR_CHUNKS[2].text, top_k=1)

    assert hit.score == pytest.approx(1.0, abs=1e-5)


def test_top_k_limits_the_number_of_hits(filled_store: ChromaVectorStore) -> None:
    assert len(filled_store.query("leave", top_k=2)) == 2


def test_top_k_below_one_is_rejected(filled_store: ChromaVectorStore) -> None:
    with pytest.raises(ValueError, match="top_k"):
        filled_store.query("leave", top_k=0)


def test_add_chunks_returns_the_number_written(
    vector_store: ChromaVectorStore,
) -> None:
    assert vector_store.add_chunks(1, "HR_Policy.pdf", HR_CHUNKS) == 3
    assert vector_store.count_for(1) == 3


def test_adding_no_chunks_writes_nothing(vector_store: ChromaVectorStore) -> None:
    assert vector_store.add_chunks(1, "empty.txt", []) == 0
    assert vector_store.count() == 0


def test_adding_the_same_document_twice_does_not_duplicate(
    vector_store: ChromaVectorStore,
) -> None:
    vector_store.add_chunks(1, "HR_Policy.pdf", HR_CHUNKS)
    vector_store.add_chunks(1, "HR_Policy.pdf", HR_CHUNKS)

    assert vector_store.count() == len(HR_CHUNKS)
    hits = vector_store.query("casual leave", top_k=10)
    assert len(hits) == len({hit.chunk_index for hit in hits})


def test_re_adding_overwrites_the_chunk_text(vector_store: ChromaVectorStore) -> None:
    vector_store.add_chunks(1, "a.txt", [Chunk(index=0, text="old wording", page=None)])
    vector_store.add_chunks(1, "a.txt", [Chunk(index=0, text="new wording", page=None)])

    [hit] = vector_store.query("wording", top_k=5)
    assert hit.text == "new wording"


def test_large_documents_are_embedded_in_batches(
    vector_store: ChromaVectorStore, fake_embedder: FakeEmbedder
) -> None:
    chunks = [Chunk(index=i, text=f"topic{i} text", page=1) for i in range(300)]

    assert vector_store.add_chunks(1, "big.pdf", chunks) == 300

    assert fake_embedder.document_batch_sizes == [128, 128, 44]
    assert vector_store.count_for(1) == 300


def test_document_ids_filter_restricts_the_search(
    filled_store: ChromaVectorStore,
) -> None:
    hits = filled_store.query("leave flights hotel", top_k=10, document_ids=[2])

    assert hits
    assert {hit.document_id for hit in hits} == {2}


def test_document_ids_filter_accepts_several_documents(
    filled_store: ChromaVectorStore,
) -> None:
    hits = filled_store.query("leave", top_k=10, document_ids=[1, 2, 99])

    assert {hit.document_id for hit in hits} == {1, 2}


def test_empty_document_ids_match_nothing(filled_store: ChromaVectorStore) -> None:
    assert filled_store.query("leave", top_k=10, document_ids=[]) == []


def test_unknown_document_id_matches_nothing(filled_store: ChromaVectorStore) -> None:
    assert filled_store.query("leave", top_k=10, document_ids=[99]) == []


def test_delete_document_removes_only_that_document(
    filled_store: ChromaVectorStore,
) -> None:
    filled_store.delete_document(1)

    assert filled_store.count_for(1) == 0
    assert filled_store.count_for(2) == len(TRAVEL_CHUNKS)
    assert {hit.document_id for hit in filled_store.query("leave", top_k=10)} == {2}


def test_deleting_an_unknown_document_is_a_no_op(
    filled_store: ChromaVectorStore,
) -> None:
    filled_store.delete_document(99)

    assert filled_store.count() == len(HR_CHUNKS) + len(TRAVEL_CHUNKS)


def test_query_on_an_empty_collection_returns_nothing(
    vector_store: ChromaVectorStore,
) -> None:
    assert vector_store.query("anything at all", top_k=5) == []


def test_chunks_survive_a_new_client_on_the_same_folder(
    chroma_dir: Path,
) -> None:
    writer = ChromaVectorStore.persistent(chroma_dir, TEST_COLLECTION, FakeEmbedder())
    writer.add_chunks(1, "HR_Policy.pdf", HR_CHUNKS)
    del writer
    # Chroma caches one in-memory system per folder; drop it so the next client
    # really has to read what was written to disk.
    SharedSystemClient.clear_system_cache()

    reader = ChromaVectorStore.persistent(chroma_dir, TEST_COLLECTION, FakeEmbedder())

    assert reader.count() == len(HR_CHUNKS)
    [hit] = reader.query("casual leave", top_k=1)
    assert (hit.document_id, hit.filename, hit.page, hit.chunk_index) == (
        1,
        "HR_Policy.pdf",
        2,
        0,
    )


def test_persistent_creates_a_missing_folder(chroma_dir: Path) -> None:
    nested = chroma_dir / "nested" / "deeper"

    ChromaVectorStore.persistent(nested, TEST_COLLECTION, FakeEmbedder())

    assert nested.is_dir()


# Chroma 1.5 records "no embedding function" as its `legacy` config type and
# warns whenever `.configuration` is read back; the store itself never reads it.
@pytest.mark.filterwarnings("ignore:legacy embedding function config")
def test_collection_uses_cosine_distance_without_an_embedding_function(
    chroma_dir: Path,
) -> None:
    client = chromadb.PersistentClient(
        path=chroma_dir, settings=ChromaSettings(anonymized_telemetry=False)
    )
    ChromaVectorStore(client, TEST_COLLECTION, FakeEmbedder())

    collection = client.get_collection(TEST_COLLECTION)
    assert collection.configuration["hnsw"]["space"] == "cosine"
    assert collection.configuration["embedding_function"] is None


def test_chroma_failures_become_vector_store_errors(
    vector_store: ChromaVectorStore, chroma_dir: Path
) -> None:
    other_dimension = ChromaVectorStore.persistent(
        chroma_dir, TEST_COLLECTION, FakeEmbedder(dimension=8)
    )
    vector_store.add_chunks(1, "HR_Policy.pdf", HR_CHUNKS)

    with pytest.raises(VectorStoreError, match="could not search") as caught:
        other_dimension.query("casual leave", top_k=1)
    assert caught.value.operation == "search the document chunks"


def test_an_invalid_collection_name_is_a_vector_store_error(chroma_dir: Path) -> None:
    with pytest.raises(VectorStoreError, match="open collection 'x'"):
        ChromaVectorStore.persistent(chroma_dir, "x", FakeEmbedder())


def test_a_data_folder_that_is_a_file_is_a_vector_store_error(
    tmp_path: Path,
) -> None:
    not_a_folder = tmp_path / "chroma"
    not_a_folder.write_text("occupied")

    with pytest.raises(VectorStoreError, match="data folder"):
        ChromaVectorStore.persistent(not_a_folder, TEST_COLLECTION, FakeEmbedder())
