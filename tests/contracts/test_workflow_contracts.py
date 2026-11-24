"""Contracts the workflow needs: clarification candidates, modification, memory."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from supplyguard.contracts import (
    ApprovalDecision,
    ConfirmedIncident,
    RecoveryAction,
    RecoveryActionType,
    RecoveryPlan,
    ResolutionMemory,
    SpendingCapSource,
    SupplierCandidate,
    Urgency,
)

DECIDED_AT = datetime(2026, 10, 19, tzinfo=UTC)
LARGER_ORDER = RecoveryAction(
    action_id="ACT-003",
    action_type=RecoveryActionType.PURCHASE_ALTERNATIVE,
    payload={"supplier_id": "S004", "component_id": "MC-17", "qty": 6000},
    cost_eur=Decimal("58800"),
)


def test_confirmed_incident_new_fields_have_safe_defaults() -> None:
    incident = ConfirmedIncident(
        case_id="CASE-001",
        user_id="u-pm-01",
        supplier_id="S001",
        component_id="MC-17",
        delay_days=12,
        scenario_date=date(2026, 10, 19),
        spending_cap_eur=Decimal("75000"),
        original_message="Atlas announced a delay for MC-17.",
    )

    assert incident.purchase_order_requires_approval is True
    assert incident.urgency is Urgency.MEDIUM
    assert incident.spending_cap_source is SpendingCapSource.MESSAGE


def test_supplier_candidate_for_ambiguous_name() -> None:
    candidates = (
        SupplierCandidate(supplier_id="S001", name="Atlas Components"),
        SupplierCandidate(supplier_id="S006", name="Atlas Logistik GmbH"),
    )

    assert [c.supplier_id for c in candidates] == ["S001", "S006"]


def test_modification_is_a_non_approval_with_changed_actions() -> None:
    decision = ApprovalDecision(
        approval_request_id="APR-001",
        case_id="CASE-001",
        payload_hash="sha256:example",
        approved=False,
        approver_user_ids=("u-pm-01",),
        decided_at=DECIDED_AT,
        modified_actions=(LARGER_ORDER,),
    )

    assert decision.is_modification


def test_approval_cannot_also_modify_actions() -> None:
    with pytest.raises(ValidationError):
        ApprovalDecision(
            approval_request_id="APR-001",
            case_id="CASE-001",
            payload_hash="sha256:example",
            approved=True,
            approver_user_ids=("u-pm-01",),
            decided_at=DECIDED_AT,
            modified_actions=(LARGER_ORDER,),
        )


def test_plan_without_feasible_option_has_no_recommendation() -> None:
    plan = RecoveryPlan(
        case_id="CASE-001",
        options=(),
        recommended_option_id=None,
        recommendation_reason="No option is within capacity and the spending cap.",
    )

    assert plan.recommended_option_id is None


def test_recommendation_must_still_reference_an_option() -> None:
    with pytest.raises(ValidationError):
        RecoveryPlan(
            case_id="CASE-001",
            options=(),
            recommended_option_id="OPT-404",
            recommendation_reason="Missing option.",
        )


def test_resolution_memory_reads_supplied_memory_format() -> None:
    supplied = (
        '{"memory_id": "MEM-HC-001", "case_id": "HC-001", "component_id": "MC-300", '
        '"supplier_id": "S049", "strategy": "Transfer available inventory", '
        '"approved_summary": "logistics disruption (5 d) resolved.", "access_scope": "internal"}'
    )

    memory = ResolutionMemory.model_validate_json(supplied)

    assert memory.strategy == "Transfer available inventory"
