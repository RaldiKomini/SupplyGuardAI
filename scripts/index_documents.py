"""Index the support-data documents and approved incident memories.

Usage:
    uv run python scripts/index_documents.py --support-data support-data --backend all
    uv run python scripts/index_documents.py --backend pgvector     # needs DATABASE_URL
    uv run python scripts/index_documents.py --backend chroma   # CHROMA_HOST/PORT or CHROMA_PATH

Elpiniki's docker-compose runs this once at setup, after PostgreSQL and Chroma are healthy.
Safe to run twice: rows are upserted by chunk_id.
"""

import argparse
from pathlib import Path

from supplyguard.retrieval.ingestion import ingest_documents, load_incident_memories
from supplyguard.retrieval.service import make_vector_store
from supplyguard.vectorstores.embeddings import get_embeddings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--support-data", default="support-data")
    parser.add_argument("--backend", choices=["pgvector", "chroma", "all"], default="all")
    args = parser.parse_args()

    root = Path(args.support_data)
    chunks, findings = ingest_documents(root / "documents")
    memories = load_incident_memories(root / "structured" / "approved_incident_memories.jsonl")
    print(
        f"Parsed {len(chunks)} chunks from {len({c.citation.source_id for c in chunks})} docs, "
        f"{len(memories)} approved incident memories."
    )
    for f in findings:
        print(f"  SECURITY FINDING {f.source_id}: {f.category} -> removed before indexing")

    embeddings = get_embeddings()
    backends = ["pgvector", "chroma"] if args.backend == "all" else [args.backend]
    for backend in backends:
        docs = make_vector_store(backend, embeddings, "documents")
        docs.add(chunks)
        mems = make_vector_store(backend, embeddings, "memories")
        mems.add(memories)
        print(f"{backend}: documents={docs.count()} memories={mems.count()} (separate collections)")


if __name__ == "__main__":
    main()
