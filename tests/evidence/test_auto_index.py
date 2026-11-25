"""index=False (what the workflow runtime uses): a fresh store is filled, a stale store is
refreshed, and an out-of-date copy in the database can never override the documents on disk."""

import shutil

import pytest

from supplyguard.retrieval.ingestion import ingest_documents
from supplyguard.retrieval.service import EvidenceService
from supplyguard.vectorstores.embeddings import HashingEmbeddings
from supplyguard.vectorstores.memory_store import InMemoryVectorStore

PROCUREMENT_MANAGER = {"public", "internal", "procurement"}


class CountingStore(InMemoryVectorStore):
    """An in-memory store that remembers every chunk id written to it."""

    def __init__(self, name: str):
        super().__init__(HashingEmbeddings(), name=name)
        self.written: list[str] = []

    def add(self, chunks):
        self.written += [c.chunk_id for c in chunks]
        super().add(chunks)


def test_empty_stores_are_filled_even_with_index_false(support_data):
    docs, mems = CountingStore("documents"), CountingStore("memories")
    service = EvidenceService.from_support_data(support_data, index=False, stores=(docs, mems))

    assert docs.count() == len(service.chunks) > 0
    assert mems.count() > 0  # the supplied approved memories
    # the vector half of the search works, not only BM25
    hits = docs.search("Atlas Components contract lead time", PROCUREMENT_MANAGER, k=3)
    assert hits


def test_index_false_does_not_rewrite_unchanged_chunks(support_data):
    docs, mems = CountingStore("documents"), CountingStore("memories")
    EvidenceService.from_support_data(support_data, index=False, stores=(docs, mems))
    docs.written.clear()
    mems.written.clear()

    # "restart" on the same stores: everything is there, so nothing is written again
    EvidenceService.from_support_data(support_data, index=False, stores=(docs, mems))
    assert docs.written == [] and mems.written == []


def test_only_missing_or_changed_chunks_are_added(support_data):
    docs, mems = CountingStore("documents"), CountingStore("memories")
    first = EvidenceService.from_support_data(support_data, index=True, stores=(docs, mems))
    removed = first.chunks[0].chunk_id
    del docs._chunks[removed], docs._vectors[removed]  # simulate one chunk missing in the store
    docs.written.clear()

    EvidenceService.from_support_data(support_data, index=False, stores=(docs, mems))
    assert docs.written == [removed]


CORDILLERA_EMAIL = "DOC-EM-CORDILLERA-01"
CORDILLERA_QUERY = "Cordillera expedite PO-2026-9002 charge 10%"


@pytest.fixture
def data_copy(support_data, tmp_path):
    """A private copy of support-data, so a test can change documents on disk."""
    return shutil.copytree(support_data, tmp_path / "support-data")


def _set_scope(data, source_id: str, old: str, new: str) -> None:
    path = next((data / "documents").rglob(f"{source_id}.md"))
    path.write_text(
        path.read_text(encoding="utf-8").replace(f"access_scope: {old}", f"access_scope: {new}"),
        encoding="utf-8",
    )


def test_tightened_scope_on_disk_is_never_bypassed_by_a_stale_store(data_copy):
    """Regression: after a restart with index=False, a document whose scope was tightened on
    disk was still returned from the old copy in the vector store (with its old scope)."""
    docs, mems = CountingStore("documents"), CountingStore("memories")
    EvidenceService.from_support_data(data_copy, index=True, stores=(docs, mems))
    _set_scope(data_copy, CORDILLERA_EMAIL, "procurement", "finance-confidential")

    # Even WITHOUT the startup refresh (the store is left stale on purpose), search uses the
    # current files: the service is built straight from the new chunks and the old store.
    new_chunks, _ = ingest_documents(data_copy / "documents")
    stale = EvidenceService(new_chunks, [], docs, mems)
    result = stale.search(CORDILLERA_QUERY, PROCUREMENT_MANAGER, k=20)
    assert CORDILLERA_EMAIL not in {e.citation.source_id for e in result.evidence}

    # And the normal restart path refreshes the stored copy as well.
    docs.written.clear()
    restarted = EvidenceService.from_support_data(data_copy, index=False, stores=(docs, mems))
    assert [cid for cid in docs.written if cid.startswith(CORDILLERA_EMAIL)] == [
        f"{CORDILLERA_EMAIL}#c0"
    ]
    assert docs._chunks[f"{CORDILLERA_EMAIL}#c0"].citation.access_scope == "finance-confidential"
    result = restarted.search(CORDILLERA_QUERY, PROCUREMENT_MANAGER, k=20)
    assert CORDILLERA_EMAIL not in {e.citation.source_id for e in result.evidence}
    finance = PROCUREMENT_MANAGER | {"finance-confidential"}
    assert CORDILLERA_EMAIL in {
        e.citation.source_id for e in restarted.search(CORDILLERA_QUERY, finance).evidence
    }


def test_document_deleted_from_disk_is_no_longer_returned(data_copy):
    docs, mems = CountingStore("documents"), CountingStore("memories")
    EvidenceService.from_support_data(data_copy, index=True, stores=(docs, mems))
    next((data_copy / "documents").rglob(f"{CORDILLERA_EMAIL}.md")).unlink()

    restarted = EvidenceService.from_support_data(data_copy, index=False, stores=(docs, mems))
    assert f"{CORDILLERA_EMAIL}#c0" in docs._chunks  # still in the vector store ...
    result = restarted.search(CORDILLERA_QUERY, PROCUREMENT_MANAGER, k=20)
    assert CORDILLERA_EMAIL not in {
        e.citation.source_id for e in result.evidence
    }  # ... never returned


def test_citations_always_carry_the_current_metadata(data_copy):
    """What the user sees (scope, status, version) comes from the files, not the database."""
    docs, mems = CountingStore("documents"), CountingStore("memories")
    service = EvidenceService.from_support_data(data_copy, index=True, stores=(docs, mems))
    current = {c.chunk_id: c.citation for c in service.chunks}
    for evidence in service.search(
        "Atlas contract lead time MC-17", PROCUREMENT_MANAGER, k=20
    ).evidence:
        assert evidence.citation == current[evidence.chunk.chunk_id]
