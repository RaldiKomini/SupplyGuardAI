"""Document ingestion: markdown files -> chunks with provenance.

Steps for each document in support-data/documents/:
  1. read the YAML-like front matter (source_id, doc_type, version, effective_date,
     access_scope, supplier_id, status ...);
  2. decide whether it is superseded (front matter says so, or a newer version of
     the same supplier's contract exists);
  3. scan the body AND the free-text metadata (title) for prompt injection, check that
     identifier fields look like identifiers, and keep only the sanitized values;
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
from supplyguard.guardrails.metadata import (
    DOCUMENT_FREE_TEXT,
    DOCUMENT_IDENTIFIERS,
    MEMORY_FREE_TEXT,
    MEMORY_IDENTIFIERS,
    SAFE_IDENTIFIER,
    clean_metadata,
    is_valid_version,
    safe_version,
)
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


def _version_stamp(meta: dict) -> tuple[date, int]:
    """(effective date, version) used to find the latest contract. Never raises:
    a missing date counts as the oldest date, an invalid version as version 1."""
    return (_to_date(meta.get("effective_date")) or date.min, safe_version(meta.get("version", 1)))


def mark_superseded(docs: list[dict]) -> None:
    """A document is superseded if its front matter says so, or if another document of
    the same type for the same supplier has a higher version and a later effective date
    (DOC-PRO-02: 'a later effective_date supersedes an earlier version').

    A contract whose version is not a valid number (e.g. 'draft') cannot be the latest
    version in force: it never supersedes another contract and is itself marked superseded.
    This runs BEFORE the metadata guardrail, so it must never raise on bad metadata."""
    latest: dict[tuple, tuple] = {}
    for d in docs:
        m = d["meta"]
        valid_version = is_valid_version(m.get("version", 1))
        if m.get("doc_type") == "contract" and m.get("supplier_id") and valid_version:
            key = ("contract", m["supplier_id"])
            stamp = _version_stamp(m)
            latest[key] = max(latest.get(key, stamp), stamp)
    for d in docs:
        m = d["meta"]
        status = str(m.get("status", "")).lower()
        key = (m.get("doc_type"), m.get("supplier_id"))
        invalid_contract = m.get("doc_type") == "contract" and not is_valid_version(
            m.get("version", 1)
        )
        older_than_latest = key in latest and _version_stamp(m) < latest[key]
        if invalid_contract or older_than_latest:
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


def _finding_source_id(doc: dict) -> str:
    """The ID we report findings under. If source_id itself is not a valid identifier we
    use the file name, so attack text never ends up in a finding."""
    sid = str(doc["meta"].get("source_id", ""))
    return sid if SAFE_IDENTIFIER.match(sid) else f"file:{Path(doc.get('path', 'unknown')).stem}"


def chunk_document(doc: dict) -> tuple[list[DocumentChunk], list[ContentFinding]]:
    source_id = _finding_source_id(doc)
    # Metadata is untrusted too: the title and IDs are shown to the model in every chunk.
    m, findings = clean_metadata(doc["meta"], source_id, DOCUMENT_FREE_TEXT, DOCUMENT_IDENTIFIERS)
    if not m.get("source_id"):
        return [], findings  # no valid source_id -> we cannot cite it, so we do not index it
    citation = Citation(
        source_id=m["source_id"],
        doc_type=m.get("doc_type") or "unknown",
        title=m.get("title", ""),
        version=safe_version(m.get("version") or 1),
        effective_date=_to_date(m.get("effective_date", "")),
        access_scope=m.get("access_scope", ""),  # empty scope -> nobody can retrieve it
        supplier_id=m.get("supplier_id", ""),
        status=m.get("status") or "current",
    )
    clean_body, body_findings = scan_and_sanitize(doc["body"], citation.source_id)
    findings += body_findings
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


def memory_to_chunk_with_findings(record: dict) -> tuple[DocumentChunk, list[ContentFinding]]:
    """Turn one approved incident memory into a chunk for the separate memory collection,
    plus the guardrail findings for it.

    Used both for the 40 supplied memories and for new ones Elena's workflow
    stores after a case is approved and closed. Every free-text field is scanned and
    every identifier is checked, exactly like document metadata."""
    raw_id = str(record.get("memory_id", ""))
    if not SAFE_IDENTIFIER.match(raw_id):
        raise ValueError("memory_id must be a plain identifier")
    r, findings = clean_metadata(record, raw_id, MEMORY_FREE_TEXT, MEMORY_IDENTIFIERS)
    memory_id = r["memory_id"]
    citation = Citation(
        source_id=memory_id,
        doc_type="approved_incident_memory",
        title=f"Approved resolution {r.get('case_id', '')} - {r.get('component_id', '')}",
        version=safe_version(r.get("version", 1)),
        effective_date=_to_date(r.get("approved_on", "")),
        # missing -> 'internal' (the default in the data); invalid -> '' (nobody can retrieve it)
        access_scope=r.get("access_scope", "internal"),
        supplier_id=r.get("supplier_id", ""),
        chunk_id=f"{memory_id}#c0",
    )
    clean, summary_findings = scan_and_sanitize(r.get("approved_summary", ""), memory_id)
    findings += summary_findings
    strategy = " ".join(str(r.get("strategy", "")).split())  # one line, so it can be parsed back
    text = (
        f"[{memory_id} | approved_incident_memory | case {r.get('case_id', '')} | "
        f"component {r.get('component_id', '')} | supplier {r.get('supplier_id', '')}]\n"
        f"Strategy: {strategy}\n"
        f"Summary: {clean}"
    )
    chunk = DocumentChunk(
        chunk_id=citation.chunk_id,
        text=text,
        citation=citation,
        security_flags=sorted({f.category for f in findings}),
    )
    return chunk, findings


def memory_to_chunk(record: dict) -> DocumentChunk:
    """Same as memory_to_chunk_with_findings, without the findings."""
    return memory_to_chunk_with_findings(record)[0]


def load_incident_memories_with_findings(
    jsonl_path: str | Path,
) -> tuple[list[DocumentChunk], list[ContentFinding]]:
    chunks, findings = [], []
    for line in Path(jsonl_path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            chunk, chunk_findings = memory_to_chunk_with_findings(json.loads(line))
            chunks.append(chunk)
            findings += chunk_findings
    return chunks, findings


def load_incident_memories(jsonl_path: str | Path) -> list[DocumentChunk]:
    return load_incident_memories_with_findings(jsonl_path)[0]


MEMORY_TEXT = re.compile(
    r"^\[(?P<memory_id>[^ |\]]+) \| approved_incident_memory \| case (?P<case_id>[^ |\]]*) \| "
    r"component (?P<component_id>[^ |\]]*) \| supplier (?P<supplier_id>[^ |\]]*)\]\n"
    r"Strategy: (?P<strategy>[^\n]*)\nSummary: (?P<approved_summary>.*)$",
    re.DOTALL,
)


def parse_memory_chunk(chunk: DocumentChunk) -> dict | None:
    """Read a memory chunk back into its fields (the reverse of memory_to_chunk).

    Works for chunks that come back from pgvector or Chroma after a restart, because all
    fields are in the chunk text. The values are the SANITIZED ones that were indexed.
    Returns None if the text does not have the expected shape."""
    match = MEMORY_TEXT.match(chunk.text)
    if not match:
        return None
    fields = match.groupdict()
    fields["access_scope"] = chunk.citation.access_scope
    return fields
