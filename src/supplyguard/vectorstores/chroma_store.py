"""ChromaDB store: the alternative vector index (used in the benchmark and as the
semantic leg of the hybrid retriever).

Connection:
  - CHROMA_HOST / CHROMA_PORT set  -> chromadb.HttpClient (the Docker service)
  - otherwise                       -> chromadb.PersistentClient at CHROMA_PATH (default ./.chroma)

Same metadata as pgvector. Scope / supplier / document-type filters are passed as
Chroma's `where` filter, so they are applied inside the database query.
"""

from __future__ import annotations

import os

from supplyguard.retrieval.models import DocumentChunk


def _client():
    import chromadb

    host = os.getenv("CHROMA_HOST")
    if host:
        return chromadb.HttpClient(host=host, port=int(os.getenv("CHROMA_PORT", "8000")))
    return chromadb.PersistentClient(path=os.getenv("CHROMA_PATH", ".chroma"))


class ChromaStore:
    def __init__(self, embeddings, collection: str = "evidence_documents", client=None):
        self.name = "chroma"
        self.embeddings = embeddings
        self.client = client or _client()
        # cosine distance, so similarity = 1 - distance (same scale as pgvector)
        self.collection = self.client.get_or_create_collection(
            collection, metadata={"hnsw:space": "cosine"}
        )

    def add(self, chunks: list[DocumentChunk]) -> None:
        if not chunks:
            return
        self.collection.upsert(
            ids=[c.chunk_id for c in chunks],
            documents=[c.text for c in chunks],
            embeddings=self.embeddings.embed_documents([c.text for c in chunks]),
            metadatas=[c.metadata() for c in chunks],
        )

    @staticmethod
    def _where(allowed_scopes, supplier_id, doc_types) -> dict:
        conditions = [{"access_scope": {"$in": sorted(allowed_scopes)}}]
        if supplier_id:
            conditions.append({"supplier_id": supplier_id})
        if doc_types:
            conditions.append({"doc_type": {"$in": list(doc_types)}})
        # Chroma needs "$and" only when there is more than one condition.
        return conditions[0] if len(conditions) == 1 else {"$and": conditions}

    def search(self, query, allowed_scopes, k=8, supplier_id=None, doc_types=None):
        if not allowed_scopes:
            return []
        result = self.collection.query(
            query_embeddings=[self.embeddings.embed_query(query)],
            n_results=k,
            where=self._where(allowed_scopes, supplier_id, doc_types),
        )
        pairs = []
        for text, meta, dist in zip(
            result["documents"][0], result["metadatas"][0], result["distances"][0], strict=True
        ):
            pairs.append((DocumentChunk.from_metadata(text, meta), 1.0 - float(dist)))
        return pairs

    def count(self) -> int:
        return self.collection.count()

    def all_chunks(self) -> list[DocumentChunk]:
        stored = self.collection.get(include=["documents", "metadatas"])
        return [
            DocumentChunk.from_metadata(text, meta)
            for text, meta in zip(stored["documents"], stored["metadatas"], strict=True)
        ]
