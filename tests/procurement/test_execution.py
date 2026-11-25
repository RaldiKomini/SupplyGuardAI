"""Approved actions run exactly once, anything not approved runs never."""

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from supplyguard.approval.policy import load_approval_policy
from supplyguard.approval.requests import build_approval_request
from supplyguard.contracts import (
    ApprovalDecision,
    ExecutionStatus,
    RecoveryAction,
    RecoveryActionType,
    RecoveryOption,
)
from supplyguard.procurement.execution import (
    InMemoryExecutionStore,
    execute_approved,
    idempotency_key,
)

POLICY = load_approval_policy(
    Path(__file__).parents[2] / "support-data" / "config" / "policies.json"
)
NOW = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)

EXPEDITE = RecoveryAction(
    action_id="ACT-EXPEDITE",
    action_type=RecoveryActionType.EXPEDITE_ORDER,
    payload={"po_id": "PO-2026-9002", "qty": 1500},
    cost_eur=Decimal("882.00"),
)
PURCHASE = RecoveryAction(
    action_id="ACT-BUY-S004",
    action_type=RecoveryActionType.PURCHASE_ALTERNATIVE,
    payload={"supplier_id": "S004", "component_id": "MC-17", "qty": 3000},
    cost_eur=Decimal("29400.00"),
)
REQUEST = build_approval_request(
    case_id="CASE-001",
    option=RecoveryOption(
        option_id="OPT-PLAN-1",
        name="Expedite and purchase",
        actions=(EXPEDITE, PURCHASE),
        recovery_cost_eur=Decimal("30282.00"),
        revenue_at_risk_eur=Decimal("0"),
        uncovered_units=850,
    ),
    policy=POLICY,
    requested_at=NOW,
)


def _decision(*, roles=("procurement_manager", "finance_controller"), payload_hash=None):
    return ApprovalDecision(
        approval_request_id=REQUEST.approval_request_id,
        case_id=REQUEST.case_id,
        payload_hash=payload_hash or REQUEST.payload_hash,
        approved=True,
        approver_user_ids=("u-pm-01", "u-fc-01")[: len(roles)],
        approver_roles=roles,
        decided_at=NOW,
    )


@pytest.mark.asyncio
async def test_approved_actions_run_once_each() -> None:
    store = InMemoryExecutionStore()

    results = await execute_approved(REQUEST, _decision(), store, now=NOW)

    assert [r.status for r in results] == [ExecutionStatus.SUCCEEDED] * 2
    assert results[1].external_reference == "SIM-PO-CASE-001-ACT-BUY-S004"
    assert len(store.results) == 2


@pytest.mark.asyncio
async def test_retry_returns_stored_outcome_without_writing_again() -> None:
    store = InMemoryExecutionStore()
    first = await execute_approved(REQUEST, _decision(), store, now=NOW)

    second = await execute_approved(REQUEST, _decision(), store, now=NOW)

    assert [r.status for r in second] == [ExecutionStatus.ALREADY_EXECUTED] * 2
    assert [r.external_reference for r in second] == [r.external_reference for r in first]
    assert all(r.status == ExecutionStatus.SUCCEEDED for r in store.results.values())


@pytest.mark.asyncio
async def test_unfinished_claim_is_neither_success_nor_a_second_write() -> None:
    store = InMemoryExecutionStore()
    for action in REQUEST.actions:
        assert await store.claim(idempotency_key(REQUEST, action), REQUEST, action)

    results = await execute_approved(REQUEST, _decision(), store, now=NOW)

    assert all(result.status is ExecutionStatus.FAILED for result in results)
    assert all(result.external_reference is None for result in results)
    assert not store.results


@pytest.mark.asyncio
async def test_missing_approver_executes_nothing() -> None:
    store = InMemoryExecutionStore()

    results = await execute_approved(
        REQUEST, _decision(roles=("procurement_manager",)), store, now=NOW
    )

    assert all(r.status == ExecutionStatus.REJECTED for r in results)
    assert "finance_controller" in results[0].message
    assert store.results == {}


@pytest.mark.asyncio
async def test_decision_for_another_payload_executes_nothing() -> None:
    store = InMemoryExecutionStore()

    results = await execute_approved(
        REQUEST, _decision(payload_hash="sha256:other"), store, now=NOW
    )

    assert all(r.status == ExecutionStatus.REJECTED for r in results)
    assert store.results == {}
