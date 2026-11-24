"""Retrieval benchmark: pgvector vs ChromaDB vs hybrid.

Each approach answers the same labelled queries as the same user, and we measure:
  - Recall@5   : share of the expected documents found in the top 5
  - MRR        : 1 / rank of the first expected document (higher = found earlier)
  - Top-1 auth.: the top result is the authoritative source we expect (e.g. contract v3, not v2)
  - Leaks      : results the user was not allowed to see (must be 0)
  - Latency    : average milliseconds per query

Approaches:
  pgvector : vector search only (baseline)
  chroma   : vector search only (alternative index)
  hybrid   : Chroma vectors + BM25 keywords + metadata filter, RRF fusion, precedence weighting

Usage:
    uv run python scripts/benchmark_retrieval.py --support-data support-data \
        --out docs/evidence/retrieval_benchmark.md
    uv run python scripts/benchmark_retrieval.py --offline   # in-memory stand-ins, no databases
"""

import argparse
import time
from pathlib import Path

from supplyguard.retrieval.hybrid import Retriever
from supplyguard.retrieval.ingestion import ingest_documents
from supplyguard.retrieval.lexical import BM25Index
from supplyguard.retrieval.service import make_vector_store
from supplyguard.vectorstores.embeddings import get_embeddings
from supplyguard.vectorstores.memory_store import InMemoryVectorStore

PM = {"public", "internal", "procurement"}
FIN = PM | {"finance-confidential"}

# (query, user scopes, expected source_ids - the first one is the authoritative answer)
GOLD = [
    ("Atlas Components contract lead time MC-17", PM, ["DOC-CON-S001-v3"]),
    ("Atlas MC-17 delay notice PO-2026-9001", PM, ["DOC-NOT-ATLAS-MC17"]),
    ("Atlas account manager says the delay could be 16 days", PM, ["DOC-EM-ATLAS-01"]),
    ("Borealis ISO 9001 certificate expiry", PM, ["DOC-QUA-S003-v1", "DOC-EM-BOREALIS-01"]),
    ("Cordillera expedite fee PO-2026-9002", PM, ["DOC-CON-S004-v1", "DOC-EM-CORDILLERA-01"]),
    ("Helios real monthly capacity for MC-17", PM, ["DOC-CON-S002-v1", "DOC-EM-HELIOS-01"]),
    ("Delta Micro wafer allocation shared with upstream partner", PM, ["DOC-EM-DELTA-01"]),
    ("which source wins when documents conflict", PM, ["DOC-PRO-02-v1"]),
    ("second source shares an upstream manufacturer", PM, ["DOC-PRO-05-v1"]),
    ("usable stock quarantined lots transfer cost", PM, ["DOC-PRO-06-v1"]),
    ("approval thresholds and incident spending cap", PM, ["DOC-PRO-03-v1"]),
    ("can the incident cap be raised to EUR 90,000", PM, ["DOC-EM-INTERNAL-01", "DOC-PRO-03-v1"]),
    ("instructions embedded in supplier documents", PM, ["DOC-PRO-11-v1"]),
    ("expired certificate blocks new orders", PM, ["DOC-PRO-04-v1", "DOC-QUA-S003-v1"]),
    ("which purchase orders can be expedited", PM, ["DOC-PRO-08-v1"]),
    ("Helios price update Q4 MC-17", PM, ["DOC-NOT-HELIOS-PRICE"]),
    ("Atlas Logistik GmbH supply contract", PM, ["DOC-CON-S006-v1"]),
    ("Cordillera Industrial qualification record", PM, ["DOC-QUA-S004-v1"]),
    ("incident report Borda Industrial MC-300", PM, ["DOC-INC-HC-001"]),
    ("gross margin by customer tier", FIN, ["DOC-FIN-01-v1"]),
    ("gross margin by customer tier", PM, []),  # must return nothing confidential
]


def build_approaches(chunks, offline: bool):
    embeddings = get_embeddings()
    if offline:
        pg = InMemoryVectorStore(embeddings, name="pgvector (in-memory stand-in)")
        ch = InMemoryVectorStore(embeddings, name="chroma (in-memory stand-in)")
        pg.add(chunks)
        ch.add(chunks)
    else:  # assumes scripts/index_documents.py has been run
        pg = make_vector_store("pgvector", embeddings, "documents")
        ch = make_vector_store("chroma", embeddings, "documents")
    bm25 = BM25Index(chunks)
    return (
        [
            Retriever(vector_store=pg, use_authority=False, name="pgvector"),
            Retriever(vector_store=ch, use_authority=False, name="chroma"),
            Retriever(vector_store=ch, lexical=bm25, use_authority=True, name="hybrid"),
        ],
        pg.name,
        ch.name,
    )


def evaluate(retriever) -> dict:
    recall, mrr, top1, leaks, timings, n_labelled = 0.0, 0.0, 0, 0, [], 0
    for query, scopes, expected in GOLD:
        start = time.perf_counter()
        result = retriever.retrieve(query, scopes, k=10)
        timings.append((time.perf_counter() - start) * 1000)
        ids = []
        for e in result.evidence:  # several chunks of one document count once
            if e.citation.source_id not in ids:
                ids.append(e.citation.source_id)
        leaks += sum(1 for e in result.evidence if e.citation.access_scope not in scopes)
        if not expected:
            continue
        n_labelled += 1
        recall += len(set(ids[:5]) & set(expected)) / len(expected)
        first = next((i for i, sid in enumerate(ids, start=1) if sid in expected), None)
        mrr += 1 / first if first else 0
        top1 += int(bool(ids) and ids[0] == expected[0])
    return {
        "recall@5": recall / n_labelled,
        "mrr": mrr / n_labelled,
        "top1": top1 / n_labelled,
        "leaks": leaks,
        "latency_ms": sum(timings) / len(timings),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--support-data", default="support-data")
    parser.add_argument("--out", default="docs/evidence/retrieval_benchmark.md")
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()

    chunks, _ = ingest_documents(Path(args.support_data) / "documents")
    approaches, pg_name, ch_name = build_approaches(chunks, args.offline)
    rows = [(r.name, evaluate(r)) for r in approaches]

    embedder = type(get_embeddings()).__name__
    n_labelled = sum(1 for g in GOLD if g[2])
    n_probes = len(GOLD) - n_labelled
    lines = [
        "# Retrieval benchmark - pgvector vs ChromaDB vs hybrid",
        "",
        f"- Documents: {len({c.citation.source_id for c in chunks})} ({len(chunks)} chunks); "
        f"labelled queries: {n_labelled} + {n_probes} scope-leak probe(s)",
        f"- Embeddings: `{embedder}`; stores: {pg_name}, {ch_name}",
        "",
        "| Approach | Recall@5 | MRR | Top-1 authoritative | Scope leaks | Avg latency (ms) |",
        "|---|---|---|---|---|---|",
    ]
    for name, m in rows:
        lines.append(
            f"| {name} | {m['recall@5']:.2f} | {m['mrr']:.2f} | {m['top1']:.2f} | {m['leaks']} | "
            f"{m['latency_ms']:.1f} |"
        )
    lines += [
        "",
        "How to read this: Recall@5 = did we find the right documents; MRR = how high the first "
        "right one ranked; Top-1 authoritative = was the #1 result the source we should plan on "
        "(e.g. contract v3, not the superseded v2). Scope leaks must be 0 for every approach "
        "because "
        "the filter runs inside each store's query.",
        "",
    ]
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
