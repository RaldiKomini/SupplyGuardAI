"""Planning and approval adapters, end to end on the MC-17 reference plan.

Two different approvers approve the 30,378 EUR plan; procurement then runs each action
once, and a retry creates no duplicate actions.
"""

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from planning.golden import IMPACT, INCIDENT

from supplyguard.approval.policy import load_approval_policy
from supplyguard.approval.revalidation import RevalidationError
from supplyguard.approval.service import DeterministicApprovalService
from supplyguard.calculations.service import DeterministicCostService
from supplyguard.contracts import ApprovalDecision, ExecutionStatus
from supplyguard.planning.service import DeterministicRecoveryPlanningService
from supplyguard.procurement import mcp_server
from supplyguard.procurement.approvals import InMemoryApprovalRecords
from supplyguard.procurement.execution import InMemoryExecutionStore
from supplyguard.procurement.mcp_client import McpProcurementClient

CONFIG = Path(__file__).resolve().parents[2] / "support-data" / "config" / "policies.json"
NOW = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
COSTS = DeterministicCostService()

pytestmark = pytest.mark.asyncio


class InProcessClient(McpProcurementClient):
    async def _call(self, tool, arguments):
        _, structured = await mcp_server.server.call_tool(tool, arguments)
        return structured


async def _case_context(case_id: str):
    return INCIDENT, IMPACT


@pytest.fixture
def setup(monkeypatch: pytest.MonkeyPatch):
    records = InMemoryApprovalRecords()
    store = InMemoryExecutionStore()
    monkeypatch.setattr(mcp_server, "_approvals", lambda: records)
    monkeypatch.setattr(mcp_server, "_store", lambda: store)
    planning = DeterministicRecoveryPlanningService.from_policy_file(CONFIG, COSTS)
    approvals = DeterministicApprovalService(
        records,
        load_approval_policy(CONFIG),
        case_context=_case_context,
        costs=COSTS,
        transfer_cost_eur_per_unit=Decimal("0.12"),
        clock=lambda: NOW,
    )
    return SimpleNamespace(
        planning=planning,
        approvals=approvals,
        records=records,
        store=store,
        procurement=InProcessClient("http://unused"),
    )


async def _plan_and_request(setup):
    plan = await setup.planning.propose(INCIDENT, SimpleNamespace(impact=IMPACT), evidence=None)
    return plan, await setup.approvals.create_request(plan)


def _vote(request, user_id: str, role: str, *, approved: bool = True) -> ApprovalDecision:
    return ApprovalDecision(
        approval_request_id=request.approval_request_id,
        case_id=request.case_id,
        payload_hash=request.payload_hash,
        approved=approved,
        approver_user_ids=(user_id,),
        approver_roles=(role,),
        decided_at=NOW,
    )


async def test_planning_recommends_the_reference_plan_without_blocking_findings(setup) -> None:
    plan, request = await _plan_and_request(setup)

    assert plan.recommended_option_id == "OPT-PLAN-1"
    assert not plan.needs_revision
    assert request.total_cost_eur == Decimal("30378.00")
    assert request.required_approver_roles == ("procurement_manager", "finance_controller")
    assert request.approval_request_id in setup.records.requests


async def test_two_separate_approvers_authorise_and_retry_creates_no_duplicates(setup) -> None:
    _, request = await _plan_and_request(setup)

    first = await setup.approvals.record_decision(
        request, _vote(request, "u-pm-01", "procurement_manager")
    )
    assert not first.approved  # one of the two required roles
    early = await setup.procurement.execute(request, first)
    assert {r.status for r in early} == {ExecutionStatus.REJECTED}
    assert setup.store.results == {}

    both = await setup.approvals.record_decision(
        request, _vote(request, "u-fc-01", "finance_controller")
    )
    assert both.approved
    assert set(both.approver_user_ids) == {"u-pm-01", "u-fc-01"}

    results = await setup.procurement.execute(request, both)
    retry = await setup.procurement.execute(request, both)

    assert {r.status for r in results} == {ExecutionStatus.SUCCEEDED}
    assert {r.status for r in retry} == {ExecutionStatus.ALREADY_EXECUTED}
    assert len(setup.store.results) == len(request.actions)
    assert [r.external_reference for r in retry] == [r.external_reference for r in results]


async def test_one_user_cannot_approve_twice(setup) -> None:
    _, request = await _plan_and_request(setup)
    await setup.approvals.record_decision(request, _vote(request, "u-pm-01", "procurement_manager"))

    with pytest.raises(ValueError, match="already decided"):
        await setup.approvals.record_decision(
            request, _vote(request, "u-pm-01", "finance_controller")
        )


async def test_a_role_the_tier_does_not_need_cannot_approve(setup) -> None:
    _, request = await _plan_and_request(setup)

    with pytest.raises(ValueError, match="role not required"):
        await setup.approvals.record_decision(request, _vote(request, "u-ro-01", "viewer"))


async def test_a_decision_for_another_payload_is_refused(setup) -> None:
    _, request = await _plan_and_request(setup)
    other = _vote(request, "u-pm-01", "procurement_manager").model_copy(
        update={"payload_hash": "sha256:other"}
    )

    with pytest.raises(ValueError, match="does not match"):
        await setup.approvals.record_decision(request, other)


async def test_a_rejection_blocks_execution(setup) -> None:
    _, request = await _plan_and_request(setup)
    await setup.approvals.record_decision(request, _vote(request, "u-pm-01", "procurement_manager"))
    combined = await setup.approvals.record_decision(
        request, _vote(request, "u-fc-01", "finance_controller", approved=False)
    )

    results = await setup.procurement.execute(request, combined)

    assert not combined.approved
    assert {r.status for r in results} == {ExecutionStatus.REJECTED}


async def test_an_edit_creates_a_new_request_and_old_approvals_do_not_carry_over(setup) -> None:
    _, request = await _plan_and_request(setup)
    await setup.approvals.record_decision(request, _vote(request, "u-pm-01", "procurement_manager"))
    await setup.approvals.record_decision(request, _vote(request, "u-fc-01", "finance_controller"))
    edited = tuple(
        a.model_copy(update={"payload": {**a.payload, "qty": 2000}})
        if a.payload.get("supplier_id") == "S004"
        else a
        for a in request.actions
    )

    new_request = await setup.approvals.revalidate(request, edited)
    _, new_decision = await setup.records.load(new_request.approval_request_id)
    results = await setup.procurement.execute(new_request, new_decision)

    assert new_request.payload_version == 2
    assert not new_decision.approved
    assert {r.status for r in results} == {ExecutionStatus.REJECTED}
    with pytest.raises(RevalidationError):
        oversized = tuple(
            a.model_copy(update={"payload": {**a.payload, "qty": 6000}})
            if a.payload.get("supplier_id") == "S004"
            else a
            for a in new_request.actions
        )
        await setup.approvals.revalidate(new_request, oversized)


async def test_a_superseded_request_cannot_execute(setup) -> None:
    _, first = await _plan_and_request(setup)
    await setup.approvals.record_decision(first, _vote(first, "u-pm-01", "procurement_manager"))
    first_decision = await setup.approvals.record_decision(
        first, _vote(first, "u-fc-01", "finance_controller")
    )
    edited = tuple(
        a.model_copy(update={"payload": {**a.payload, "qty": 2000}})
        if a.payload.get("supplier_id") == "S004"
        else a
        for a in first.actions
    )
    second = await setup.approvals.revalidate(first, edited)

    stale = await setup.procurement.execute(first, first_decision)

    assert {r.status for r in stale} == {ExecutionStatus.REJECTED}
    assert "superseded by version 2" in stale[0].message
    assert setup.store.results == {}

    # 20,578 EUR after the edit: below 25,000, so the procurement manager alone approves.
    assert second.required_approver_roles == ("procurement_manager",)
    second_decision = await setup.approvals.record_decision(
        second, _vote(second, "u-pm-01", "procurement_manager")
    )
    current = await setup.procurement.execute(second, second_decision)

    assert {r.status for r in current} == {ExecutionStatus.SUCCEEDED}
    purchase = next(a for a in second.actions if a.payload.get("supplier_id") == "S004")
    assert purchase.payload["qty"] == 2000
    assert len(setup.store.results) == len(second.actions)
