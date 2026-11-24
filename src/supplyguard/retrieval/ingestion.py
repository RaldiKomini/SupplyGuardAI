"""Document ingestion: markdown files -> chunks with provenance.

Steps for each document in support-data/documents/:
  1. read the YAML-like front matter (source_id, doc_type, version, effective_date,
     access_scope, supplier_id, status ...);
  2. decide whether it is superseded (front matter says so, or a newer version of
     the same supplier's contract exists);
  3. scan for prompt injection and keep only the sanitized text;
  4. split into chunks and put a provenance header at the top of EVERY chunk,
     so a claim is never separated from its source.

Approved incident memories (support-data/structured/approved_incident_memories.jsonl)
become chunks too, but they go into a separate collection.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

from supplyguard.guardrails.injection import scan_and_sanitize
from supplyguard.retrieval.models import Citation, ContentFinding, DocumentChunk

MAX_CHUNK_CHARS = 1500


def parse_front_matter(raw: str) -> tuple[dict, str]:
    """Split '---\\nkey: value\\n---\\nbody' into (metadata dict, body)."""
    raw = raw.replace("\r\n", "\n")
    match = re.match(r"^---\n(.*?)\n---\n(.*)$", raw, re.DOTALL)
    if not match:
        return {}, raw
    meta = {}
    for line in match.group(1).splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            meta[key.strip()] = value.strip()
    return meta, match.group(2).strip()


def _to_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def load_documents(documents_dir: str | Path) -> list[dict]:
    """Read every .md file. Returns a list of {meta, body, path} dicts."""
    docs = []
    for path in sorted(Path(documents_dir).rglob("*.md")):
        meta, body = parse_front_matter(path.read_text(encoding="utf-8"))
        if not meta.get("source_id"):
            continue  # not a document of the data package
        docs.append({"meta": meta, "body": body, "path": str(path)})
    mark_superseded(docs)
    return docs


def mark_superseded(docs: list[dict]) -> None:
    """A document is superseded if its front matter says so, or if another document of
    the same type for the same supplier has a higher version and a later effective date
    (DOC-PRO-02: 'a later effective_date supersedes an earlier version')."""
    latest: dict[tuple, tuple] = {}
    for d in docs:
        m = d["meta"]
        if m.get("doc_type") == "contract" and m.get("supplier_id"):
            key = ("contract", m["supplier_id"])
            stamp = (_to_date(m.get("effective_date")) or date.min, int(m.get("version", 1)))
            latest[key] = max(latest.get(key, stamp), stamp)
    for d in docs:
        m = d["meta"]
        status = m.get("status", "").lower()
        key = (m.get("doc_type"), m.get("supplier_id"))
        if key in latest:
            stamp = (_to_date(m.get("effective_date")) or date.min, int(m.get("version", 1)))
            if stamp < latest[key]:
                status = "superseded"
        m["status"] = "superseded" if status == "superseded" else "current"


def _split_body(body: str) -> list[str]:
    """Split on blank lines, then pack paragraphs into chunks of at most MAX_CHUNK_CHARS.
    Our documents are short, so most become one chunk; a contract keeps all its
    clauses together, which is what we want for comparing terms."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
    chunks, current = [], ""
    for p in paragraphs:
        if p.startswith("# "):  # the title line is already in the provenance header
            continue
        if current and len(current) + len(p) + 2 > MAX_CHUNK_CHARS:
            chunks.append(current)
            current = p
        else:
            current = f"{current}\n\n{p}" if current else p
    if current:
        chunks.append(current)
    return chunks or [body]


def _provenance_header(c: Citation) -> str:
    status = " | SUPERSEDED" if c.status == "superseded" else ""
    supplier = f" | supplier {c.supplier_id}" if c.supplier_id else ""
    return (
        f"[{c.source_id} | {c.doc_type} | v{c.version} | effective {c.effective_date}"
        f"{supplier}{status}] {c.title}"
    )


def chunk_document(doc: dict) -> tuple[list[DocumentChunk], list[ContentFinding]]:
    m = doc["meta"]
    citation = Citation(
        source_id=m["source_id"],
        doc_type=m.get("doc_type", "unknown"),
        title=m.get("title", ""),
        version=int(m.get("version") or 1),
        effective_date=_to_date(m.get("effective_date", "")),
        access_scope=m.get("access_scope", ""),  # empty scope -> nobody can retrieve it
        supplier_id=m.get("supplier_id", ""),
        status=m.get("status", "current"),
    )
    clean_body, findings = scan_and_sanitize(doc["body"], citation.source_id)
    flags = sorted({f.category for f in findings})
    chunks = []
    for i, piece in enumerate(_split_body(clean_body)):
        chunk_id = f"{citation.source_id}#c{i}"
        chunks.append(
            DocumentChunk(
                chunk_id=chunk_id,
                text=f"{_provenance_header(citation)}\n{piece}",
                citation=citation.model_copy(update={"chunk_id": chunk_id}),
                security_flags=flags,
            )
        )
    return chunks, findings


def ingest_documents(documents_dir: str | Path) -> tuple[list[DocumentChunk], list[ContentFinding]]:
    """Main entry point: all chunks + all security findings found at ingestion."""
    all_chunks, all_findings = [], []
    for doc in load_documents(documents_dir):
        chunks, findings = chunk_document(doc)
        all_chunks.extend(chunks)
        all_findings.extend(findings)
    return all_chunks, all_findings


def memory_to_chunk(record: dict) -> DocumentChunk:
    """Turn one approved incident memory into a chunk for the separate memory collection.

    Used both for the 40 supplied memories and for new ones Elena's workflow
    stores after a case is approved and closed."""
    memory_id = record["memory_id"]
    citation = Citation(
        source_id=memory_id,
        doc_type="approved_incident_memory",
        title=f"Approved resolution {record.get('case_id', '')} - {record.get('component_id', '')}",
        version=int(record.get("version", 1)),
        effective_date=_to_date(record.get("approved_on", "")),
        access_scope=record.get("access_scope", "internal"),
        supplier_id=record.get("supplier_id", ""),
        chunk_id=f"{memory_id}#c0",
    )
    clean, findings = scan_and_sanitize(record.get("approved_summary", ""), memory_id)
    text = (
        f"[{memory_id} | approved_incident_memory | case {record.get('case_id', '')} | "
        f"component {record.get('component_id', '')} | supplier {record.get('supplier_id', '')}]\n"
        f"Strategy: {record.get('strategy', '')}. {clean}"
    )
    return DocumentChunk(
        chunk_id=citation.chunk_id,
        text=text,
        citation=citation,
        security_flags=sorted({f.category for f in findings}),
    )


def load_incident_memories(jsonl_path: str | Path) -> list[DocumentChunk]:
    lines = Path(jsonl_path).read_text(encoding="utf-8").splitlines()
    return [memory_to_chunk(json.loads(line)) for line in lines if line.strip()]
