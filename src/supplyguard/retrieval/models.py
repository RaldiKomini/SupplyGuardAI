"""INTERNAL data models of George's evidence layer.

Other components never see these classes. At the boundary, contract_mapping.py
converts them into the shared contracts in supplyguard.contracts
(EvidenceBundle, EvidenceItem, EvidenceConflict, SecurityFinding).

We keep internal models because they hold more detail than the public contract
(e.g. the extracted fact values), and they can change without a contract PR.
Every piece of text still carries its provenance (source_id, document type,
version, effective date, access scope, status).
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field


class Citation(BaseModel):
    """Where a claim comes from. Shown next to every claim in reports and the UI."""

    source_id: str
    doc_type: str
    title: str = ""
    version: int = 1
    effective_date: date | None = None
    access_scope: str
    supplier_id: str = ""
    status: str = "current"  # "current" or "superseded"
    chunk_id: str = ""

    def short(self) -> str:
        """Human-readable citation, e.g. 'DOC-CON-S001-v3 (contract v3, effective 2026-03-01)'."""
        return (
            f"{self.source_id} ({self.doc_type} v{self.version}, effective {self.effective_date})"
        )


class DocumentChunk(BaseModel):
    """A piece of a document. The text starts with a provenance header, so a claim
    can never be separated from its source when it is placed in a prompt."""

    chunk_id: str
    text: str
    citation: Citation
    # Guardrail results computed at ingestion time (category names, e.g. "approval_bypass").
    security_flags: list[str] = Field(default_factory=list)

    def metadata(self) -> dict:
        """Flat metadata for vector stores.
        Chroma only accepts str/int/float/bool, so there are no None values."""
        c = self.citation
        return {
            "chunk_id": self.chunk_id,
            "source_id": c.source_id,
            "doc_type": c.doc_type,
            "title": c.title,
            "version": int(c.version),
            "effective_date": c.effective_date.isoformat() if c.effective_date else "",
            "access_scope": c.access_scope,
            "supplier_id": c.supplier_id or "",
            "status": c.status,
            "security_flags": ",".join(self.security_flags),
        }

    @classmethod
    def from_metadata(cls, text: str, meta: dict) -> DocumentChunk:
        """Rebuild a chunk from what a vector store returns."""
        eff = meta.get("effective_date") or None
        return cls(
            chunk_id=meta["chunk_id"],
            text=text,
            citation=Citation(
                source_id=meta["source_id"],
                doc_type=meta["doc_type"],
                title=meta.get("title", ""),
                version=int(meta.get("version", 1)),
                effective_date=date.fromisoformat(str(eff)) if eff else None,
                access_scope=meta["access_scope"],
                supplier_id=meta.get("supplier_id", ""),
                status=meta.get("status", "current"),
                chunk_id=meta["chunk_id"],
            ),
            security_flags=[f for f in str(meta.get("security_flags", "")).split(",") if f],
        )


class ContentFinding(BaseModel):
    """A suspected prompt injection found inside untrusted content.
    (Mapped to the shared contracts.SecurityFinding at the boundary.)"""

    source_id: str
    category: str  # e.g. approval_bypass, data_exfiltration, false_certification ...
    severity: str  # "high" or "medium"
    snippet: str  # short excerpt, so a human can verify the finding
    action_taken: str = "removed from model context and ignored"


class Evidence(BaseModel):
    """One retrieved chunk plus why it was ranked where it is."""

    chunk: DocumentChunk
    score: float
    retrieval_method: str  # "pgvector", "chroma", "bm25" or "hybrid"
    authority_rank: int  # 1 = most authoritative (contract) ... 5 = informal chat

    @property
    def citation(self) -> Citation:
        return self.chunk.citation


class Fact(BaseModel):
    """A single value read from a document, e.g. Atlas lead time = 14 days."""

    supplier_id: str
    # lead_time_days, monthly_capacity_units, expedite_fee_pct, delay_days, certification_status
    name: str
    value: int | float | str
    citation: Citation
    authority_rank: int


class Conflict(BaseModel):
    """Two or more sources disagree. We never hide this; we pick a planning value
    using the precedence rules and keep the others as sensitivities."""

    supplier_id: str
    fact_name: str
    planning_value: int | float | str
    planning_source: Citation
    alternatives: list[
        dict
    ]  # [{"value": 16, "source": Citation, "use_as": "sensitivity"|"ignore: superseded"}]
    explanation: str


class SupplierFacts(BaseModel):
    """Everything the documents say about one supplier, already resolved by precedence."""

    supplier_id: str
    planning_values: dict[str, Fact]
    conflicts: list[Conflict]
    certification_valid: bool | None = None
    security_findings: list[ContentFinding] = Field(default_factory=list)


class RetrievalResult(BaseModel):
    """What the evidence service returns to the workflow."""

    query: str
    strategy: str
    evidence: list[Evidence]
    conflicts: list[Conflict] = Field(default_factory=list)
    security_findings: list[ContentFinding] = Field(default_factory=list)
    # Results removed by the final scope check. The stores already filter inside the
    # query, so this should always be 0; a non-zero value means a store ignored the
    # filter (we log it, and the removed items never leave this module).
    excluded_by_scope: int = 0
    uncertain: bool = False
    notes: list[str] = Field(default_factory=list)
