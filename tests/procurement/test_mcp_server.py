"""The MCP write tool executes only approved payloads, once each."""

import pytest

from supplyguard.procurement import mcp_server
from supplyguard.procurement.execution import InMemoryExecutionStore

from .test_execution import REQUEST, _decision

TOOLS = {
    "get_inventory",
    "get_supplier_options",
    "get_open_purchase_orders",
    "get_execution_result",
    "execute_approved_request",
}


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> InMemoryExecutionStore:
    memory = InMemoryExecutionStore()
    monkeypatch.setattr(mcp_server, "_store", lambda: memory)
    return memory


async def _execute(decision) -> list[dict]:
    output = await mcp_server.server.call_tool(
        "execute_approved_request",
        {
            "approval_request": REQUEST.model_dump(mode="json"),
            "approval_decision": decision.model_dump(mode="json"),
        },
    )
    _, structured = output
    return structured["results"]


@pytest.mark.asyncio
async def test_only_the_expected_tools_are_exposed() -> None:
    names = {tool.name for tool in await mcp_server.server.list_tools()}

    assert names == TOOLS


@pytest.mark.asyncio
async def test_approved_request_runs_once(store: InMemoryExecutionStore) -> None:
    first = await _execute(_decision())
    second = await _execute(_decision())

    assert [r["status"] for r in first] == ["succeeded", "succeeded"]
    assert [r["status"] for r in second] == ["already_executed", "already_executed"]
    assert len(store.results) == 2


@pytest.mark.asyncio
async def test_tampered_payload_is_not_executed(store: InMemoryExecutionStore) -> None:
    payload = REQUEST.model_dump(mode="json")
    payload["actions"][1]["payload"]["qty"] = 9000

    output = await mcp_server.server.call_tool(
        "execute_approved_request",
        {"approval_request": payload, "approval_decision": _decision().model_dump(mode="json")},
    )
    _, structured = output

    assert {r["status"] for r in structured["results"]} == {"rejected"}
    assert store.results == {}


@pytest.mark.asyncio
async def test_execution_result_lookup(store: InMemoryExecutionStore) -> None:
    (first, _) = await _execute(_decision())

    _, found = await mcp_server.server.call_tool(
        "get_execution_result", {"idempotency_key": first["idempotency_key"]}
    )
    _, missing = await mcp_server.server.call_tool(
        "get_execution_result", {"idempotency_key": "unknown"}
    )

    assert found["result"]["external_reference"] == first["external_reference"]
    assert missing == {"found": False}
