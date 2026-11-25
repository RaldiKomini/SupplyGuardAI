"""Which approval requests are still waiting for a given user, in the in-memory store."""

import asyncio
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from supplyguard.contracts import ApprovalRequest, RecoveryAction, RecoveryActionType
from supplyguard.procurement.approvals import (
    APPROVE,
    DecisionRecord,
    InMemoryApprovalRecords,
    is_open_for,
)

PM = "u-pm-01"
FC = "u-fc-01"
ANALYST = "u-an-01"
TIER_TWO = ("procurement_manager", "finance_controller")
NOW = datetime(2026, 4, 1, tzinfo=UTC)


def _request(
    request_id: str = "req-1", version: int = 1, roles: tuple[str, ...] = TIER_TWO
) -> ApprovalRequest:
    action = RecoveryAction(
        action_id=f"act-{request_id}",
        action_type=RecoveryActionType.EXPEDITE_ORDER,
        payload={"order_id": "PO-1"},
        cost_eur=Decimal("40000.00"),
        expected_completion_date=date(2026, 5, 1),
    )
    return ApprovalRequest(
        approval_request_id=request_id,
        case_id="case-1",
        option_id="opt-1",
        payload_version=version,
        payload_hash=f"hash-{request_id}-{version}",
        actions=(action,),
        total_cost_eur=Decimal("40000.00"),
        required_approver_roles=roles,
        evidence_source_ids=("src-1",),
        requested_at=NOW,
    )


def _record(user_id: str, role: str, decision: str = APPROVE) -> DecisionRecord:
    return DecisionRecord(user_id=user_id, role=role, decision=decision, decided_at=NOW)


def _store_with(request: ApprovalRequest) -> InMemoryApprovalRecords:
    store = InMemoryApprovalRecords()
    asyncio.run(store.save_request(request))
    return store


def _open_for(store: InMemoryApprovalRecords, role: str, user_id: str) -> list[str]:
    requests = asyncio.run(store.list_open_for(role, user_id))
    return [request.approval_request_id for request in requests]


def test_tier_two_request_is_open_for_each_required_role_before_any_decision() -> None:
    store = _store_with(_request())

    assert _open_for(store, "procurement_manager", PM) == ["req-1"]
    assert _open_for(store, "finance_controller", FC) == ["req-1"]


def test_request_is_not_listed_for_a_role_that_is_not_required() -> None:
    store = _store_with(_request())

    assert _open_for(store, "operations_analyst", ANALYST) == []


def test_partial_approval_leaves_the_request_open_for_the_other_role_only() -> None:
    store = _store_with(_request())
    asyncio.run(store.save_decision("req-1", _record(PM, "procurement_manager")))

    assert _open_for(store, "finance_controller", FC) == ["req-1"]
    assert _open_for(store, "procurement_manager", PM) == []
    assert asyncio.run(store.has_decided("req-1", PM)) is True
    assert asyncio.run(store.has_decided("req-1", FC)) is False


def test_full_approval_closes_the_request_for_everyone() -> None:
    store = _store_with(_request())
    asyncio.run(store.save_decision("req-1", _record(PM, "procurement_manager")))
    asyncio.run(store.save_decision("req-1", _record(FC, "finance_controller")))

    assert _open_for(store, "finance_controller", FC) == []
    assert _open_for(store, "procurement_manager", PM) == []


def test_rejection_closes_the_request() -> None:
    store = _store_with(_request())
    asyncio.run(store.save_decision("req-1", _record(PM, "procurement_manager", "reject")))

    assert _open_for(store, "finance_controller", FC) == []


def test_superseded_version_is_not_open_once_a_newer_version_exists() -> None:
    store = _store_with(_request("req-1", version=1))
    revised = _request("req-2", version=2)
    asyncio.run(store.save_revision("req-1", _record(PM, "procurement_manager"), revised))

    assert _open_for(store, "finance_controller", FC) == ["req-2"]
    assert is_open_for(_request("req-1", 1), [], latest_version=2, user_id=FC) is False


def test_same_user_cannot_decide_twice() -> None:
    store = _store_with(_request())
    asyncio.run(store.save_decision("req-1", _record(PM, "procurement_manager")))

    with pytest.raises(ValueError, match="already decided"):
        asyncio.run(store.save_decision("req-1", _record(PM, "procurement_manager")))


def test_has_decided_is_false_for_a_request_with_no_decisions() -> None:
    store = _store_with(_request())

    assert asyncio.run(store.has_decided("req-1", PM)) is False


def test_get_request_returns_the_saved_request() -> None:
    request = _request()
    store = _store_with(request)

    assert asyncio.run(store.get_request("req-1")) == request


def test_get_request_returns_none_for_an_unknown_id() -> None:
    store = _store_with(_request())

    assert asyncio.run(store.get_request("missing")) is None
