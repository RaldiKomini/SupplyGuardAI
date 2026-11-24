"""Recovery planning, approval, and execution contracts."""

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import AwareDatetime, Field, model_validator

from supplyguard.contracts.common import ContractModel, SourceReference


class RecoveryActionType(StrEnum):
    TRANSFER_INVENTORY = "transfer_inventory"
    EXPEDITE_ORDER = "expedite_order"
    PURCHASE_ALTERNATIVE = "purchase_alternative"
    REALLOCATE_ORDERS = "reallocate_orders"
    RESCHEDULE_PRODUCTION = "reschedule_production"


class RecoveryAction(ContractModel):
    action_id: str
    action_type: RecoveryActionType
    payload: dict[str, Any]
    cost_eur: Decimal = Field(ge=0)
    expected_completion_date: date | None = None
    sources: tuple[SourceReference, ...] = ()


class RecoveryOption(ContractModel):
    option_id: str
    name: str
    actions: tuple[RecoveryAction, ...]
    recovery_cost_eur: Decimal = Field(ge=0)
    revenue_at_risk_eur: Decimal = Field(ge=0)
    uncovered_units: int = Field(ge=0)
    assumptions: tuple[str, ...] = ()
    rejection_reasons: tuple[str, ...] = ()
    policy_warnings: tuple[str, ...] = ()
    sources: tuple[SourceReference, ...] = ()


class RecoveryPlan(ContractModel):
    case_id: str
    options: tuple[RecoveryOption, ...]
    recommended_option_id: str | None  # None when no option is feasible
    recommendation_reason: str
    residual_risks: tuple[str, ...] = ()

    @model_validator(mode="after")
    def recommended_option_exists(self) -> "RecoveryPlan":
        option_ids = {option.option_id for option in self.options}
        if self.recommended_option_id is not None and self.recommended_option_id not in option_ids:
            raise ValueError("recommended_option_id must reference one of the options")
        return self


class ApprovalRequest(ContractModel):
    approval_request_id: str
    case_id: str
    option_id: str
    payload_version: int = Field(ge=1)
    payload_hash: str = Field(min_length=1)
    actions: tuple[RecoveryAction, ...]
    total_cost_eur: Decimal = Field(ge=0)
    required_approver_roles: tuple[str, ...]
    evidence_source_ids: tuple[str, ...]
    requested_at: AwareDatetime


class ApprovalDecision(ContractModel):
    approval_request_id: str
    case_id: str
    payload_hash: str
    approved: bool
    approver_user_ids: tuple[str, ...]
    decided_at: AwareDatetime
    reason: str | None = None
    # A modification: not approved, with the changed actions (e.g. a new quantity).
    # They are revalidated and need a new approval request.
    modified_actions: tuple[RecoveryAction, ...] = ()

    @model_validator(mode="after")
    def approved_decision_has_approver(self) -> "ApprovalDecision":
        if self.approved and not self.approver_user_ids:
            raise ValueError("an approved decision requires at least one approver")
        return self

    @model_validator(mode="after")
    def modification_is_not_an_approval(self) -> "ApprovalDecision":
        if self.approved and self.modified_actions:
            raise ValueError("an approval cannot modify actions; changes need a new approval")
        return self

    @property
    def is_modification(self) -> bool:
        return not self.approved and bool(self.modified_actions)


class ExecutionStatus(StrEnum):
    SUCCEEDED = "succeeded"
    REJECTED = "rejected"
    FAILED = "failed"
    ALREADY_EXECUTED = "already_executed"


class ExecutionResult(ContractModel):
    case_id: str
    idempotency_key: str
    status: ExecutionStatus
    message: str
    executed_at: AwareDatetime | None = None
    external_reference: str | None = None
