"""Approved resolution summaries kept for future cases.

Fields follow support-data/structured/approved_incident_memories.jsonl, so the supplied
memories and new ones share one shape. The workflow creates them; the memory store indexes them.
"""

from pydantic import Field

from supplyguard.contracts.common import ContractModel, SourceReference


class ResolutionMemory(ContractModel):
    memory_id: str = Field(min_length=1)
    case_id: str = Field(min_length=1)
    component_id: str = Field(min_length=1)
    supplier_id: str = Field(min_length=1)
    strategy: str = Field(min_length=1)
    approved_summary: str = Field(min_length=1)
    access_scope: str = "internal"
    sources: tuple[SourceReference, ...] = ()
