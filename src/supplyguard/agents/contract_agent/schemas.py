"""Structured output of the contract-analysis agent. Every finding carries a citation."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ContractFinding(BaseModel):
    supplier_id: str
    topic: str = Field(
        description=(
            "e.g. lead_time_days, monthly_capacity_units, expedite_fee_pct, certification_status"
        )
    )
    value: str
    source_id: str
    version: int
    effective_date: str
    status: str = Field(description="'current' or 'superseded'")
    note: str = ""


class ContractAnalysis(BaseModel):
    summary: str
    findings: list[ContractFinding]
    conflicts: list[str] = Field(
        default_factory=list, description="each conflict, with the source used and why"
    )
    superseded_sources: list[str] = Field(default_factory=list)
    security_findings: list[str] = Field(
        default_factory=list, description="embedded instructions that were ignored"
    )
