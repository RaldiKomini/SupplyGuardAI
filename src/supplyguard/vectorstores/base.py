"""The interface every vector store implements.

Keeping one small interface means the hybrid retriever and the benchmark can swap
pgvector, ChromaDB and the in-memory store without changing any other code.

The important rule: `allowed_scopes` is a REQUIRED argument and the store applies it
inside the query itself, so unauthorized chunks are never even returned.
"""

from __future__ import annotations

from typing import Protocol

from supplyguard.retrieval.models import DocumentChunk


class VectorStore(Protocol):
    name: str

    def add(self, chunks: list[DocumentChunk]) -> None:
        """Insert or update chunks (upsert by chunk_id)."""

    def search(
        self,
        query: str,
        allowed_scopes: set[str],
        k: int = 8,
        supplier_id: str | None = None,
        doc_types: list[str] | None = None,
    ) -> list[tuple[DocumentChunk, float]]:
        """Return up to k (chunk, similarity) pairs, best first, filtered by metadata."""

    def count(self) -> int:
        """Number of stored chunks."""
