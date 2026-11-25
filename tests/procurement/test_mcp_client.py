"""The workflow-side client returns typed results from the procurement MCP server."""

from datetime import UTC, datetime

import pytest

from supplyguard.contracts import ExecutionStatus
from supplyguard.contracts.protocols import ProcurementExecutionService
from supplyguard.procurement import mcp_server
from supplyguard.procurement.approvals import DecisionRecord, InMemoryApprovalRecords
from supplyguard.procurement.execution import InMemoryExecutionStore
from supplyguard.procurement.mcp_client import McpProcurementClient

from .test_execution import REQUEST, _decision

DECIDED = datetime(2026, 10, 19, 13, 0, tzinfo=UTC)


class InProcessClient(McpProcurementClient):
    """Calls the server object directly instead of over HTTP."""

    async def _call(self, tool, arguments):
        _, structured = await mcp_server.server.call_tool(tool, arguments)
        return structured


@pytest.fixture
def approvals(monkeypatch: pytest.MonkeyPatch) -> InMemoryApprovalRecords:
    records = InMemoryApprovalRecords()
    records.requests[REQUEST.approval_request_id] = REQUEST
    monkeypatch.setattr(mcp_server, "_approvals", lambda: records)
    memory = InMemoryExecutionStore()
    monkeypatch.setattr(mcp_server, "_store", lambda: memory)
    return records


@pytest.mark.asyncio
async def test_client_returns_one_typed_result_per_action(
    approvals: InMemoryApprovalRecords,
) -> None:
    approvals.decisions[REQUEST.approval_request_id] = [
        DecisionRecord("u-pm-01", "procurement_manager", "approve", DECIDED),
        DecisionRecord("u-fc-01", "finance_controller", "approve", DECIDED),
    ]
    client: ProcurementExecutionService = InProcessClient("http://unused")

    results = await client.execute(REQUEST, _decision())

    assert [r.status for r in results] == [ExecutionStatus.SUCCEEDED] * 2
    assert results[1].external_reference == "SIM-PO-CASE-001-ACT-BUY-S004"


@pytest.mark.asyncio
async def test_a_decision_sent_by_the_caller_is_not_trusted(
    approvals: InMemoryApprovalRecords,
) -> None:
    # Nothing approved in the tables: the caller's own "approved" decision changes nothing.
    client = InProcessClient("http://unused")

    results = await client.execute(REQUEST, _decision())

    assert {r.status for r in results} == {ExecutionStatus.REJECTED}
