"""The workflow-side client returns typed results from the procurement MCP server."""

import pytest

from supplyguard.contracts import ExecutionStatus
from supplyguard.contracts.protocols import ProcurementExecutionService
from supplyguard.procurement import mcp_server
from supplyguard.procurement.execution import InMemoryExecutionStore
from supplyguard.procurement.mcp_client import McpProcurementClient

from .test_execution import REQUEST, _decision


class InProcessClient(McpProcurementClient):
    """Calls the server object directly instead of over HTTP."""

    async def _call(self, tool, arguments):
        _, structured = await mcp_server.server.call_tool(tool, arguments)
        return structured


@pytest.mark.asyncio
async def test_client_returns_one_typed_result_per_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    memory = InMemoryExecutionStore()
    monkeypatch.setattr(mcp_server, "_store", lambda: memory)
    client: ProcurementExecutionService = InProcessClient("http://unused")

    results = await client.execute(REQUEST, _decision())

    assert [r.status for r in results] == [ExecutionStatus.SUCCEEDED] * 2
    assert results[1].external_reference == "SIM-PO-CASE-001-ACT-BUY-S004"
