"""Exact-payload approval against the MC-17 reference plan (golden_scenario.json)."""

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from supplyguard.approval.hashing import payload_hash
from supplyguard.approval.policy import load_approval_policy, required_approver_roles
from supplyguard.approval.requests import (
    build_approval_request,
    decision_problems,
    revalidate_request,
)
from supplyguard.contracts import (
    ApprovalDecision,
    RecoveryAction,
    RecoveryActionType,
    RecoveryOption,
)

POLICY = load_approval_policy(
    Path(__file__).parents[2] / "support-data" / "config" / "policies.json"
)
NOW = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)

TRANSFER = RecoveryAction(
    action_id="ACT-1",
    action_type=RecoveryActionType.TRANSFER_INVENTORY,
    payload={"component_id": "MC-17", "from_location": "W-HUB", "to_location": "W-LYO", "qty": 800},
    cost_eur=Decimal("96.00"),
)
EXPEDITE = RecoveryAction(
    action_id="ACT-2",
    action_type=RecoveryActionType.EXPEDITE_ORDER,
    payload={"po_id": "PO-2026-9002", "days_gain": 7, "fee_pct": "6"},
    cost_eur=Decimal("882.00"),
)
PURCHASE = RecoveryAction(
    action_id="ACT-3",
    action_type=RecoveryActionType.PURCHASE_ALTERNATIVE,
    payload={"supplier_id": "S004", "component_id": "MC-17", "qty": 3000, "unit_price_eur": "9.80"},
    cost_eur=Decimal("29400.00"),
    expected_completion_date=date(2026, 10, 28),
)
PLAN_1 = RecoveryOption(
    option_id="OPT-PLAN-1",
    name="Transfer + expedite + Cordillera",
    actions=(TRANSFER, EXPEDITE, PURCHASE),
    recovery_cost_eur=Decimal("30378.00"),
    revenue_at_risk_eur=Decimal("0"),
    uncovered_units=850,
)


def _request():
    return build_approval_request(
        case_id="CASE-001", option=PLAN_1, policy=POLICY, requested_at=NOW
    )


def _decision(request, *, user_ids, roles, payload=None, approved=True):
    return ApprovalDecision(
        approval_request_id=request.approval_request_id,
        case_id=request.case_id,
        payload_hash=payload or request.payload_hash,
        approved=approved,
        approver_user_ids=user_ids,
        approver_roles=roles,
        decided_at=NOW,
    )


@pytest.mark.parametrize(
    ("amount", "roles"),
    [
        ("25000", ("procurement_manager",)),
        ("25000.01", ("procurement_manager", "finance_controller")),
        ("75000", ("procurement_manager", "finance_controller")),
        ("90000", ("finance_director",)),
    ],
)
def test_approval_tiers_follow_policy(amount: str, roles: tuple[str, ...]) -> None:
    assert required_approver_roles(Decimal(amount), POLICY) == roles


def test_amount_above_highest_tier_cannot_be_approved() -> None:
    with pytest.raises(ValueError):
        required_approver_roles(Decimal("90000.01"), POLICY)


def test_reference_plan_needs_two_approvers() -> None:
    request = _request()

    assert request.total_cost_eur == Decimal("30378.00")
    assert request.required_approver_roles == ("procurement_manager", "finance_controller")
    assert request.payload_version == 1


def test_hash_is_stable_and_changes_with_quantity() -> None:
    changed = PURCHASE.model_copy(update={"payload": {**PURCHASE.payload, "qty": 2999}})

    assert payload_hash((TRANSFER, EXPEDITE, PURCHASE)) == payload_hash(
        (TRANSFER, EXPEDITE, PURCHASE)
    )
    assert payload_hash((TRANSFER, EXPEDITE, changed)) != payload_hash(
        (TRANSFER, EXPEDITE, PURCHASE)
    )


def test_both_required_roles_authorise_execution() -> None:
    request = _request()
    decision = _decision(
        request,
        user_ids=("u-pm-01", "u-fc-01"),
        roles=("procurement_manager", "finance_controller"),
    )

    assert decision_problems(request, decision) == ()


def test_one_approver_is_not_enough_above_25k() -> None:
    request = _request()
    decision = _decision(request, user_ids=("u-pm-01",), roles=("procurement_manager",))

    assert any("finance_controller" in problem for problem in decision_problems(request, decision))


def test_rejection_authorises_nothing() -> None:
    request = _request()
    decision = _decision(request, user_ids=(), roles=(), approved=False)

    assert decision_problems(request, decision)


def test_modified_quantity_needs_new_approval() -> None:
    request = _request()
    old_decision = _decision(
        request,
        user_ids=("u-pm-01", "u-fc-01"),
        roles=("procurement_manager", "finance_controller"),
    )
    six_thousand = PURCHASE.model_copy(
        update={"payload": {**PURCHASE.payload, "qty": 6000}, "cost_eur": Decimal("58800.00")}
    )

    new_request = revalidate_request(
        request, (TRANSFER, EXPEDITE, six_thousand), policy=POLICY, requested_at=NOW
    )

    assert new_request.payload_version == 2
    assert new_request.payload_hash != request.payload_hash
    assert decision_problems(new_request, old_decision)
