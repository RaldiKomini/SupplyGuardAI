"""Open approvals and decided checks read the stored decisions, as the Chainlit inbox needs."""

import asyncio
import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from procurement.test_execution import NOW, REQUEST
from testcontainers.community.postgres import PostgresContainer

from supplyguard.data.database import Database
from supplyguard.procurement.approvals import DecisionRecord, PostgresApprovalRecords

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_INTEGRATION") != "1", reason="set RUN_INTEGRATION=1"),
]
ROOT = Path(__file__).resolve().parents[2]


def test_inbox_follows_stored_decisions(monkeypatch: pytest.MonkeyPatch) -> None:
    with PostgresContainer("postgres:17-alpine", driver=None) as postgres:
        url = postgres.get_connection_url()
        monkeypatch.setenv("DATABASE_URL", url)
        config = Config(str(ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(ROOT / "migrations"))
        command.upgrade(config, "head")
        asyncio.run(follow_decisions(url))


async def follow_decisions(url: str) -> None:
    database = Database(url)
    try:
        records = PostgresApprovalRecords(database)
        request_id = REQUEST.approval_request_id
        await records.save_request(REQUEST)

        assert await ids_for(records, "procurement_manager", "u-pm-01") == [request_id]
        assert await ids_for(records, "finance_controller", "u-fc-01") == [request_id]
        assert await ids_for(records, "operations_analyst", "u-an-01") == []

        await records.save_decision(
            request_id, DecisionRecord("u-pm-01", "procurement_manager", "approve", NOW)
        )
        assert await records.has_decided(request_id, "u-pm-01") is True
        assert await records.has_decided(request_id, "u-fc-01") is False
        assert await ids_for(records, "procurement_manager", "u-pm-01") == []
        assert await ids_for(records, "finance_controller", "u-fc-01") == [request_id]

        await records.save_decision(
            request_id, DecisionRecord("u-fc-01", "finance_controller", "approve", NOW)
        )
        assert await ids_for(records, "finance_controller", "u-fc-01") == []
    finally:
        await database.close()


async def ids_for(records: PostgresApprovalRecords, role: str, user_id: str) -> list[str]:
    requests = await records.list_open_for(role, user_id)
    return [request.approval_request_id for request in requests]
