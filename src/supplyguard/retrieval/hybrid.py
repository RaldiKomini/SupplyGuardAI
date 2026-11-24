"""Hybrid retrieval = metadata filter + keyword (BM25) + semantic (vector) search.

Pipeline for one query:
  1. Metadata filter  - access scope (always), supplier and document type (optional).
                        Applied inside each search, before scoring.
  2. Two searches     - BM25 and the vector store each return their top candidates.
  3. Fusion           - Reciprocal Rank Fusion (RRF): score = sum of 1 / (60 + rank).
                        RRF only uses ranks, so we never have to compare a BM25 score
                        with a cosine similarity directly.
  4. Deduplicate      - by chunk_id (the same chunk found by both searches counts once).
  5. Authority        - add a small precedence bonus (contracts above e-mails) and a
                        penalty for superseded versions (they stay visible, marked).
                        Small on purpose: relevance first, authority breaks near-ties.
  6. Final scope check, guardrail findings and conflict detection.

The same class also runs "vector only" (lexical=None) or "BM25 only"
(vector_store=None), which is how the benchmark compares approaches fairly.
"""

from __future__ import annotations

from supplyguard.guardrails.access import enforce_scope
from supplyguard.retrieval.models import Evidence, RetrievalResult
from supplyguard.retrieval.precedence import authority_rank, ranking_adjustment

RRF_K = 60


class Retriever:
    def __init__(
        self, vector_store=None, lexical=None, use_authority: bool = True, name: str | None = None
    ):
        if vector_store is None and lexical is None:
            raise ValueError("need at least one search method")
        self.vector_store = vector_store
        self.lexical = lexical
        self.use_authority = use_authority
        if name:
            self.name = name
        elif vector_store is not None and lexical is not None:
            self.name = "hybrid"
        else:
            self.name = (vector_store or lexical).name

    def retrieve(
        self,
        query: str,
        allowed_scopes: set[str],
        k: int = 8,
        supplier_id: str | None = None,
        doc_types: list[str] | None = None,
        candidates: int = 20,
    ) -> RetrievalResult:
        ranked_lists = []
        for searcher in (self.vector_store, self.lexical):
            if searcher is not None:
                ranked_lists.append(
                    searcher.search(
                        query,
                        allowed_scopes,
                        k=candidates,
                        supplier_id=supplier_id,
                        doc_types=doc_types,
                    )
                )

        fused: dict[str, dict] = {}
        for results in ranked_lists:
            for rank, (chunk, _score) in enumerate(results, start=1):
                entry = fused.setdefault(chunk.chunk_id, {"chunk": chunk, "score": 0.0})
                entry["score"] += 1.0 / (RRF_K + rank)

        evidence = []
        for entry in fused.values():
            c = entry["chunk"].citation
            score = entry["score"] + (
                ranking_adjustment(c.doc_type, c.status) if self.use_authority else 0.0
            )
            evidence.append(
                Evidence(
                    chunk=entry["chunk"],
                    score=round(score, 6),
                    retrieval_method=self.name,
                    authority_rank=authority_rank(c.doc_type),
                )
            )
        evidence.sort(key=lambda e: e.score, reverse=True)

        evidence, removed = enforce_scope(evidence, allowed_scopes)  # defence in depth
        return RetrievalResult(
            query=query, strategy=self.name, evidence=evidence[:k], excluded_by_scope=removed
        )
