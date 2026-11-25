"""With the real approval and procurement adapters wired into the graph, a two-role tier
pauses after the first approval and executes once, after the second."""

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from test_approval_flow import CONFIG, golden_model, start

from supplyguard.approval.policy import load_approval_policy
from supplyguard.approval.service import DeterministicApprovalService
from supplyguard.calculations.service import DeterministicCostService
from supplyguard.procurement import mcp_server
from supplyguard.procurement.approvals import InMemoryApprovalRecords
from supplyguard.procurement.execution import InMemoryExecutionStore
from supplyguard.procurement.mcp_client import McpProcurementClient
from supplyguard.workflow.graph import build_graph

POLICY = Path(__file__).resolve().parents[2] / "support-data" / "config" / "policies.json"


class InProcessClient(McpProcurementClient):
    async def _call(self, tool, arguments):
        _, structured = await mcp_server.server.call_tool(tool, arguments)
        return structured


async def _no_context(case_id: str):
    raise AssertionError("this test does not edit the plan")


@pytest.fixture
def real_adapters(monkeypatch: pytest.MonkeyPatch):
    records, store = InMemoryApprovalRecords(), InMemoryExecutionStore()
    monkeypatch.setattr(mcp_server, "_approvals", lambda: records)
    monkeypatch.setattr(mcp_server, "_store", lambda: store)
    approval = DeterministicApprovalService(
        records,
        load_approval_policy(POLICY),
        case_context=_no_context,
        costs=DeterministicCostService(),
        transfer_cost_eur_per_unit=Decimal("0.12"),
    )
    return approval, InProcessClient("http://unused"), store


def _approve(graph, request: dict, user_id: str, role: str) -> dict:
    decision = {
        "approval_request_id": request["approval_request_id"],
        "case_id": request["case_id"],
        "payload_hash": request["payload_hash"],
        "approved": True,
        "approver_user_ids": [user_id],
        "approver_roles": [role],
        "decided_at": datetime(2026, 10, 19, 12, tzinfo=UTC).isoformat(),
    }
    return asyncio.run(graph.ainvoke(Command(resume=decision), CONFIG))


def test_case_waits_for_the_second_approver_then_executes_once(
    scripted_model, make_services, real_adapters
) -> None:
    approval, execution, store = real_adapters
    services = make_services(approval=approval, execution=execution)
    graph = build_graph(
        model=golden_model(scripted_model), services=services, checkpointer=InMemorySaver()
    )
    request = start(graph)
    assert request["required_approver_roles"] == ["procurement_manager", "finance_controller"]

    after_first = _approve(graph, request, "u-pm-01", "procurement_manager")

    assert "__interrupt__" in after_first  # paused again, not failed
    assert after_first["status"] == "awaiting_approval"
    assert "finance_controller" in after_first["messages"][-1].content
    assert store.results == {}

    after_second = _approve(graph, request, "u-fc-01", "finance_controller")

    assert after_second["status"] == "completed"
    assert len(store.results) == 1  # the plan's one action, executed once
