"""In-memory vector store.

Used by the unit tests (no database needed) and as a stand-in when pgvector or
Chroma are not running. Same filtering rules as the real stores.
"""

from __future__ import annotations

import numpy as np

from supplyguard.retrieval.models import DocumentChunk


class InMemoryVectorStore:
    def __init__(self, embeddings, name: str = "memory"):
        self.embeddings = embeddings
        self.name = name
        self._chunks: dict[str, DocumentChunk] = {}
        self._vectors: dict[str, np.ndarray] = {}

    def add(self, chunks: list[DocumentChunk]) -> None:
        if not chunks:
            return
        vectors = self.embeddings.embed_documents([c.text for c in chunks])
        for chunk, vec in zip(chunks, vectors, strict=True):
            v = np.asarray(vec, dtype=float)
            self._chunks[chunk.chunk_id] = chunk
            self._vectors[chunk.chunk_id] = v / (np.linalg.norm(v) or 1.0)

    def search(self, query, allowed_scopes, k=8, supplier_id=None, doc_types=None):
        q = np.asarray(self.embeddings.embed_query(query), dtype=float)
        q = q / (np.linalg.norm(q) or 1.0)
        scored = []
        for cid, chunk in self._chunks.items():
            c = chunk.citation
            # Metadata filter BEFORE scoring, exactly like the WHERE clause in pgvector.
            if c.access_scope not in allowed_scopes:
                continue
            if supplier_id and c.supplier_id != supplier_id:
                continue
            if doc_types and c.doc_type not in doc_types:
                continue
            scored.append((chunk, float(self._vectors[cid] @ q)))
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[:k]

    def count(self) -> int:
        return len(self._chunks)

    def all_chunks(self) -> list[DocumentChunk]:
        return list(self._chunks.values())
