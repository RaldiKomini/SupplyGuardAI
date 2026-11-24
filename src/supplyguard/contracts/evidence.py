"""Evidence and retrieval contracts."""

from datetime import date

from pydantic import Field

from supplyguard.contracts.common import ContractModel, SecurityFinding, SourceReference


class EvidenceItem(ContractModel):
    evidence_id: str
    title: str
    document_type: str
    content: str
    version: str | None = None
    effective_date: date | None = None
    status: str | None = None
    relevance_score: float | None = Field(default=None, ge=0, le=1)
    source: SourceReference


class EvidenceConflict(ContractModel):
    subject: str
    description: str
    selected_evidence_id: str | None = None
    conflicting_evidence_ids: tuple[str, ...]
    resolution_reason: str | None = None


class EvidenceBundle(ContractModel):
    case_id: str
    query: str
    items: tuple[EvidenceItem, ...]
    conflicts: tuple[EvidenceConflict, ...] = ()
    security_findings: tuple[SecurityFinding, ...] = ()
    applied_access_scopes: tuple[str, ...] = ()
