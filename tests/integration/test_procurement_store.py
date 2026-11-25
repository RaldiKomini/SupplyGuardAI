"""Stored approvals gate execution, and one outcome is kept per key across restarts."""

import asyncio
import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from procurement.test_execution import NOW, REQUEST
from sqlalchemy import func, select
from testcontainers.community.postgres import PostgresContainer

from supplyguard.contracts import ExecutionStatus
from supplyguard.data.database import Database
from supplyguard.procurement.approvals import DecisionRecord, PostgresApprovalRecords
from supplyguard.procurement.execution import execute_approved
from supplyguard.procurement.store import PostgresExecutionStore
from supplyguard.procurement.tables import audit_log, executed_actions

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_INTEGRATION") != "1", reason="set RUN_INTEGRATION=1"),
]
ROOT = Path(__file__).resolve().parents[2]


def test_execution_survives_a_restart(monkeypatch: pytest.MonkeyPatch) -> None:
    with PostgresContainer("postgres:17-alpine", driver=None) as postgres:
        url = postgres.get_connection_url()
        monkeypatch.setenv("DATABASE_URL", url)
        config = Config(str(ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(ROOT / "migrations"))
        command.upgrade(config, "head")
        asyncio.run(run_twice(url))


async def run_twice(url: str) -> None:
    first_db, second_db = Database(url), Database(url)
    try:
        records = PostgresApprovalRecords(first_db)
        await records.save_request(REQUEST)
        assert (await records.load(REQUEST.approval_request_id))[1].approved is False

        for user_id, role in (
            ("u-pm-01", "procurement_manager"),
            ("u-fc-01", "finance_controller"),
        ):
            record = DecisionRecord(user_id, role, "approve", NOW)
            await records.save_decision(REQUEST.approval_request_id, record)
        request, decision = await records.load(REQUEST.approval_request_id)
        assert request == REQUEST

        first = await execute_approved(request, decision, PostgresExecutionStore(first_db), now=NOW)
        # A new store and connection pool, as after a restart of the MCP server.
        second = await execute_approved(
            request, decision, PostgresExecutionStore(second_db), now=NOW
        )

        assert [r.status for r in first] == [ExecutionStatus.SUCCEEDED] * 2
        assert [r.status for r in second] == [ExecutionStatus.ALREADY_EXECUTED] * 2
        assert [r.external_reference for r in second] == [r.external_reference for r in first]
        async with second_db.connection() as connection:
            rows = await connection.scalar(select(func.count()).select_from(executed_actions))
            events = await connection.scalar(select(func.count()).select_from(audit_log))
        assert rows == 2
        assert events == 4  # claimed and succeeded, for each action
    finally:
        await first_db.close()
        await second_db.close()
