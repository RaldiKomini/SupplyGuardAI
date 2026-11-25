"""The MCP write tool executes only what stored decisions approve, once each."""

from datetime import UTC, datetime

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from supplyguard.procurement import mcp_server
from supplyguard.procurement.approvals import DecisionRecord, InMemoryApprovalRecords
from supplyguard.procurement.execution import InMemoryExecutionStore

from .test_execution import REQUEST

TOOLS = {
    "get_inventory",
    "get_supplier_options",
    "get_open_purchase_orders",
    "get_execution_result",
    "execute_approved_request",
}
DECIDED = datetime(2026, 10, 19, 13, 0, tzinfo=UTC)
MANAGER = DecisionRecord("u-pm-01", "procurement_manager", "approve", DECIDED)
CONTROLLER = DecisionRecord("u-fc-01", "finance_controller", "approve", DECIDED)


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> InMemoryExecutionStore:
    memory = InMemoryExecutionStore()
    monkeypatch.setattr(mcp_server, "_store", lambda: memory)
    return memory


@pytest.fixture
def approvals(monkeypatch: pytest.MonkeyPatch) -> InMemoryApprovalRecords:
    records = InMemoryApprovalRecords()
    records.requests[REQUEST.approval_request_id] = REQUEST
    monkeypatch.setattr(mcp_server, "_approvals", lambda: records)
    return records


async def _execute(approval_request_id: str = REQUEST.approval_request_id) -> list[dict]:
    _, structured = await mcp_server.server.call_tool(
        "execute_approved_request", {"approval_request_id": approval_request_id}
    )
    return structured["results"]


@pytest.mark.asyncio
async def test_only_the_expected_tools_are_exposed() -> None:
    tools = {tool.name: tool for tool in await mcp_server.server.list_tools()}

    assert tools.keys() == TOOLS
    # The caller cannot hand over a decision: the write tool takes only the request id.
    assert set(tools["execute_approved_request"].inputSchema["properties"]) == {
        "approval_request_id"
    }


@pytest.mark.asyncio
async def test_stored_approvals_run_the_request_once(
    store: InMemoryExecutionStore, approvals: InMemoryApprovalRecords
) -> None:
    approvals.decisions[REQUEST.approval_request_id] = [MANAGER, CONTROLLER]

    first = await _execute()
    second = await _execute()

    assert [r["status"] for r in first] == ["succeeded", "succeeded"]
    assert [r["status"] for r in second] == ["already_executed", "already_executed"]
    assert len(store.results) == 2


@pytest.mark.asyncio
async def test_missing_stored_approver_executes_nothing(
    store: InMemoryExecutionStore, approvals: InMemoryApprovalRecords
) -> None:
    approvals.decisions[REQUEST.approval_request_id] = [MANAGER]

    results = await _execute()

    assert {r["status"] for r in results} == {"rejected"}
    assert "finance_controller" in results[0]["message"]
    assert store.results == {}


@pytest.mark.asyncio
async def test_a_stored_rejection_blocks_execution(
    store: InMemoryExecutionStore, approvals: InMemoryApprovalRecords
) -> None:
    rejection = DecisionRecord("u-fc-01", "finance_controller", "reject", DECIDED)
    approvals.decisions[REQUEST.approval_request_id] = [MANAGER, rejection]

    results = await _execute()

    assert {r["status"] for r in results} == {"rejected"}
    assert store.results == {}


@pytest.mark.asyncio
async def test_tampered_stored_payload_is_not_executed(
    store: InMemoryExecutionStore, approvals: InMemoryApprovalRecords
) -> None:
    purchase = REQUEST.actions[1]
    bigger = purchase.model_copy(update={"payload": {**purchase.payload, "qty": 9000}})
    approvals.requests[REQUEST.approval_request_id] = REQUEST.model_copy(
        update={"actions": (REQUEST.actions[0], bigger)}
    )
    approvals.decisions[REQUEST.approval_request_id] = [MANAGER, CONTROLLER]

    results = await _execute()

    assert {r["status"] for r in results} == {"rejected"}
    assert store.results == {}


@pytest.mark.asyncio
async def test_unknown_request_is_an_error(
    store: InMemoryExecutionStore, approvals: InMemoryApprovalRecords
) -> None:
    with pytest.raises(ToolError, match="unknown approval request"):
        await _execute("APR-UNKNOWN")


@pytest.mark.asyncio
async def test_execution_result_lookup(
    store: InMemoryExecutionStore, approvals: InMemoryApprovalRecords
) -> None:
    approvals.decisions[REQUEST.approval_request_id] = [MANAGER, CONTROLLER]
    (first, _) = await _execute()

    _, found = await mcp_server.server.call_tool(
        "get_execution_result", {"idempotency_key": first["idempotency_key"]}
    )
    _, missing = await mcp_server.server.call_tool(
        "get_execution_result", {"idempotency_key": "unknown"}
    )

    assert found["result"]["external_reference"] == first["external_reference"]
    assert missing == {"found": False}
