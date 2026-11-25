"""index=False (what the workflow runtime uses) must not leave a fresh store empty."""

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


def test_index_false_never_rewrites_what_is_already_stored(support_data):
    docs, mems = CountingStore("documents"), CountingStore("memories")
    EvidenceService.from_support_data(support_data, index=False, stores=(docs, mems))
    docs.written.clear()
    mems.written.clear()

    # "restart" on the same stores: everything is there, so nothing is written again
    EvidenceService.from_support_data(support_data, index=False, stores=(docs, mems))
    assert docs.written == [] and mems.written == []


def test_only_missing_chunks_are_added(support_data):
    docs, mems = CountingStore("documents"), CountingStore("memories")
    first = EvidenceService.from_support_data(support_data, index=True, stores=(docs, mems))
    removed = first.chunks[0].chunk_id
    del docs._chunks[removed], docs._vectors[removed]  # simulate one chunk missing in the store
    docs.written.clear()

    EvidenceService.from_support_data(support_data, index=False, stores=(docs, mems))
    assert docs.written == [removed]
