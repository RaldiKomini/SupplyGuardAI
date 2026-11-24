"""Application and evaluation event contracts."""

from enum import StrEnum
from typing import Any

from pydantic import AwareDatetime, Field

from supplyguard.contracts.common import ContractModel


class UIEventType(StrEnum):
    PROGRESS = "progress"
    EVIDENCE = "evidence"
    OPTIONS = "options"
    APPROVAL = "approval"
    OUTCOME = "outcome"
    ERROR = "error"


class UIEvent(ContractModel):
    case_id: str
    event_type: UIEventType
    stage: str
    message: str
    occurred_at: AwareDatetime
    payload: dict[str, Any] = Field(default_factory=dict)


class EvaluationStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"


class EvaluationResult(ContractModel):
    evaluation_case_id: str
    category: str
    status: EvaluationStatus
    metrics: dict[str, float] = Field(default_factory=dict)
    findings: tuple[str, ...] = ()
    trace_id: str | None = None
