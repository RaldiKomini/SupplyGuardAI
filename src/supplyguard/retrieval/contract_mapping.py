"""Convert internal evidence models into the shared contracts (supplyguard.contracts).

This is the only file that knows both sides. If the shared contract changes,
this is the only file of the evidence layer that needs to change.

  internal                      shared contract
  --------                      ---------------
  Citation                  ->  SourceReference
  Evidence / DocumentChunk  ->  EvidenceItem
  Conflict                  ->  EvidenceConflict
  ContentFinding            ->  SecurityFinding
"""

from __future__ import annotations

from datetime import UTC, datetime

from supplyguard.contracts import (
    EvidenceConflict,
    EvidenceItem,
    SecurityFinding,
    Severity,
    SourceReference,
)
from supplyguard.retrieval.models import Citation, Conflict, ContentFinding, DocumentChunk


def to_source_reference(c: Citation, retrieved_at: datetime | None = None) -> SourceReference:
    return SourceReference(
        source_id=c.source_id,
        source_type=c.doc_type,
        record_id=c.chunk_id or None,
        effective_date=c.effective_date,
        retrieved_at=retrieved_at or datetime.now(UTC),
        access_scope=c.access_scope,
    )


def to_evidence_item(
    chunk: DocumentChunk, relevance: float | None, retrieved_at: datetime
) -> EvidenceItem:
    c = chunk.citation
    return EvidenceItem(
        evidence_id=chunk.chunk_id,
        title=c.title or c.source_id,
        document_type=c.doc_type,
        content=chunk.text,  # sanitized text, starts with the provenance header
        version=str(c.version),
        effective_date=c.effective_date,
        status=c.status,
        relevance_score=None if relevance is None else round(min(max(relevance, 0.0), 1.0), 4),
        source=to_source_reference(c, retrieved_at),
    )


def to_evidence_conflict(conflict: Conflict) -> EvidenceConflict:
    """The shared contract has no numeric fields, so the values are in `description`
    (e.g. 'expedite_fee_pct: planning value 6 from DOC-CON-S004-v1 ... says 10 ...')."""
    alternatives = conflict.alternatives
    return EvidenceConflict(
        subject=f"{conflict.supplier_id}.{conflict.fact_name}",
        description=conflict.explanation,
        selected_evidence_id=conflict.planning_source.chunk_id or None,
        conflicting_evidence_ids=tuple(a["source"].chunk_id for a in alternatives),
        resolution_reason=(
            "Source precedence (DOC-PRO-02): contract > qualification > notice > e-mail > chat; "
            "a later effective date supersedes an earlier version. "
            + "; ".join(
                f"{a['source'].source_id} = {a['value']} -> {a['use_as']}" for a in alternatives
            )
        ),
    )


def to_security_finding(
    finding: ContentFinding, source: Citation | None, count: int = 1
) -> SecurityFinding:
    """High-severity injections are BLOCKING; all of them were removed (blocked=True).

    The message deliberately does NOT repeat the attack text: this finding may be shown
    to a model later (e.g. in the incident report), and quoting the attack would put
    it back into a prompt. The snippet stays in the red-team report only.
    `count`: how many times this type was found in this source (see to_security_findings)."""
    times = f" (found {count} times)" if count > 1 else ""
    return SecurityFinding(
        finding_type=f"prompt_injection.{finding.category}",
        message=(
            f"Embedded instruction of type '{finding.category}' found in {finding.source_id}"
            f"{times}; {finding.action_taken}."
        ),
        severity=Severity.BLOCKING if finding.severity == "high" else Severity.WARNING,
        blocked=True,
        sources=(to_source_reference(source),) if source else (),
    )


def to_security_findings(
    findings: list[ContentFinding], citations: dict[str, Citation] | None = None
) -> tuple[SecurityFinding, ...]:
    """One SecurityFinding per (source, type), in first-seen order.

    The detector reports every match, so one document can hold the same attack type
    twice (the Helios notice has two 'system_impersonation' phrases). The report needs
    each type once per document, with the number of matches in the message."""
    groups: dict[tuple[str, str], list[ContentFinding]] = {}
    for f in findings:
        groups.setdefault((f.source_id, f.category), []).append(f)
    citations = citations or {}
    return tuple(
        to_security_finding(same[0], citations.get(source_id), count=len(same))
        for (source_id, _), same in groups.items()
    )
