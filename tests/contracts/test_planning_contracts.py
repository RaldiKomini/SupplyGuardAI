"""Contract tests for recovery planning, approval, and execution."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from supplyguard.contracts import (
    ApprovalDecision,
    ChallengeFinding,
    ExecutionResult,
    ExecutionStatus,
    RecoveryActionType,
    RecoveryOption,
    RecoveryPlan,
    Severity,
)

DECIDED_AT = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)


def _option(option_id: str) -> RecoveryOption:
    return RecoveryOption(
        option_id=option_id,
        name=option_id,
        actions=(),
        recovery_cost_eur=Decimal("0"),
        revenue_at_risk_eur=Decimal("0"),
        uncovered_units=0,
    )


def _plan(*findings: ChallengeFinding) -> RecoveryPlan:
    return RecoveryPlan(
        case_id="CASE-001",
        options=(_option("OPT-1"),),
        recommended_option_id="OPT-1",
        recommendation_reason="Only option.",
        challenge_findings=findings,
    )


def test_approved_decision_records_one_role_per_approver() -> None:
    decision = ApprovalDecision(
        approval_request_id="APR-001",
        case_id="CASE-001",
        payload_hash="sha256:abc",
        approved=True,
        approver_user_ids=("u-pm-01", "u-fc-01"),
        approver_roles=("procurement_manager", "finance_controller"),
        decided_at=DECIDED_AT,
    )

    assert decision.approver_roles == ("procurement_manager", "finance_controller")


def test_approved_decision_without_roles_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ApprovalDecision(
            approval_request_id="APR-001",
            case_id="CASE-001",
            payload_hash="sha256:abc",
            approved=True,
            approver_user_ids=("u-pm-01",),
            decided_at=DECIDED_AT,
        )


def test_rejection_needs_no_approver() -> None:
    decision = ApprovalDecision(
        approval_request_id="APR-001",
        case_id="CASE-001",
        payload_hash="sha256:abc",
        approved=False,
        approver_user_ids=(),
        decided_at=DECIDED_AT,
        reason="Prefer to wait for Atlas.",
    )

    assert not decision.approved


def test_execution_result_identifies_the_action() -> None:
    result = ExecutionResult(
        case_id="CASE-001",
        approval_request_id="APR-001",
        action_id="ACT-003",
        action_type=RecoveryActionType.PURCHASE_ALTERNATIVE,
        idempotency_key="CASE-001:ACT-003",
        status=ExecutionStatus.SUCCEEDED,
        message="Purchase order created.",
        executed_at=DECIDED_AT,
        external_reference="PO-SIM-0001",
    )

    assert result.action_id == "ACT-003"


def test_plan_with_blocking_finding_needs_revision() -> None:
    plan = _plan(
        ChallengeFinding(
            check="minimum_strategies",
            message="Fewer than three strategies were compared.",
            severity=Severity.BLOCKING,
        )
    )

    assert plan.needs_revision


def test_plan_with_only_warnings_does_not_need_revision() -> None:
    plan = _plan(
        ChallengeFinding(
            check="shared_upstream",
            message="Delta shares upstream Veltro with Atlas.",
            severity=Severity.WARNING,
            option_id="OPT-1",
        )
    )

    assert not plan.needs_revision


def test_same_user_cannot_approve_twice() -> None:
    with pytest.raises(ValidationError):
        ApprovalDecision(
            approval_request_id="APR-001",
            case_id="CASE-001",
            payload_hash="sha256:abc",
            approved=True,
            approver_user_ids=("u-pm-01", "u-pm-01"),
            approver_roles=("procurement_manager", "finance_controller"),
            decided_at=DECIDED_AT,
        )
