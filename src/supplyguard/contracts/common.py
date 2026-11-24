"""Models used by every SupplyGuard AI component."""

from datetime import date
from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class ContractModel(BaseModel):
    """Immutable, strict base class for public component contracts."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    BLOCKING = "blocking"


class SourceReference(ContractModel):
    source_id: str = Field(min_length=1)
    source_type: str = Field(min_length=1)
    record_id: str | None = None
    effective_date: date | None = None
    retrieved_at: AwareDatetime | None = None
    access_scope: str | None = None


class DataQualityWarning(ContractModel):
    issue_type: str = Field(min_length=1)
    message: str = Field(min_length=1)
    severity: Severity
    record_id: str | None = None
    sources: tuple[SourceReference, ...] = ()


class SecurityFinding(ContractModel):
    finding_type: str = Field(min_length=1)
    message: str = Field(min_length=1)
    severity: Severity
    blocked: bool = True
    sources: tuple[SourceReference, ...] = ()
