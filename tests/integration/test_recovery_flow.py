"""On PostgreSQL: two approvers authorise the reference plan, and a retry writes nothing twice."""

import asyncio
import os
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic import command
from alembic.config import Config
from planning.golden import IMPACT, INCIDENT
from sqlalchemy import func, select
from testcontainers.community.postgres import PostgresContainer

from supplyguard.approval.policy import load_approval_policy
from supplyguard.approval.service import DeterministicApprovalService
from supplyguard.calculations.service import DeterministicCostService
from supplyguard.contracts import ApprovalDecision, ExecutionStatus
from supplyguard.data.database import Database
from supplyguard.planning.service import DeterministicRecoveryPlanningService
from supplyguard.procurement.approvals import PostgresApprovalRecords
from supplyguard.procurement.execution import execute_approved
from supplyguard.procurement.store import PostgresExecutionStore
from supplyguard.procurement.tables import executed_actions

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_INTEGRATION") != "1", reason="set RUN_INTEGRATION=1"),
]
ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "support-data" / "config" / "policies.json"
NOW = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)


def test_two_approvers_and_retry_on_postgres(monkeypatch: pytest.MonkeyPatch) -> None:
    with PostgresContainer("postgres:17-alpine", driver=None) as postgres:
        url = postgres.get_connection_url()
        monkeypatch.setenv("DATABASE_URL", url)
        config = Config(str(ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(ROOT / "migrations"))
        command.upgrade(config, "head")
        asyncio.run(run_flow(url))


async def _case_context(case_id: str):
    return INCIDENT, IMPACT


def _vote(request, user_id: str, role: str) -> ApprovalDecision:
    return ApprovalDecision(
        approval_request_id=request.approval_request_id,
        case_id=request.case_id,
        payload_hash=request.payload_hash,
        approved=True,
        approver_user_ids=(user_id,),
        approver_roles=(role,),
        decided_at=NOW,
    )


async def run_flow(url: str) -> None:
    database = Database(url)
    try:
        costs = DeterministicCostService()
        records = PostgresApprovalRecords(database)
        approvals = DeterministicApprovalService(
            records,
            load_approval_policy(CONFIG),
            case_context=_case_context,
            costs=costs,
            transfer_cost_eur_per_unit=Decimal("0.12"),
            clock=lambda: NOW,
        )
        planning = DeterministicRecoveryPlanningService.from_policy_file(CONFIG, costs)
        plan = await planning.propose(INCIDENT, SimpleNamespace(impact=IMPACT), evidence=None)
        request = await approvals.create_request(plan)
        assert request.total_cost_eur == Decimal("30378.00")

        await approvals.record_decision(request, _vote(request, "u-pm-01", "procurement_manager"))
        edited = tuple(
            action.model_copy(update={"payload": {**action.payload, "qty": 2000}})
            if action.payload.get("supplier_id") == "S004"
            else action
            for action in request.actions
        )
        refused = _vote(request, "u-pm-01", "procurement_manager").model_copy(
            update={"approved": False, "modified_actions": edited}
        )
        with pytest.raises(ValueError, match="already decided"):
            await approvals.record_modification(request, refused)
        assert await records.latest_version(request.case_id) == 1
        await approvals.record_decision(request, _vote(request, "u-fc-01", "finance_controller"))
        stored_request, decision = await records.load(request.approval_request_id)
        assert decision.approved

        store = PostgresExecutionStore(database)
        first = await execute_approved(stored_request, decision, store, now=NOW)
        retry = await execute_approved(stored_request, decision, store, now=NOW)

        assert {r.status for r in first} == {ExecutionStatus.SUCCEEDED}
        assert {r.status for r in retry} == {ExecutionStatus.ALREADY_EXECUTED}
        async with database.connection() as connection:
            rows = await connection.scalar(select(func.count()).select_from(executed_actions))
        assert rows == len(request.actions)

        other = await approvals.create_request(plan.model_copy(update={"case_id": "CASE-EDIT"}))
        edit = refused.model_copy(
            update={"approval_request_id": other.approval_request_id, "case_id": other.case_id}
        )
        revised = await approvals.record_modification(other, edit)
        assert revised.payload_version == 2
        assert await approvals.record_modification(other, edit) == revised
        assert await records.latest_version(other.case_id) == 2
    finally:
        await database.close()
