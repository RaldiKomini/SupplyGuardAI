"""With the real approval and procurement adapters wired into the graph, a two-role tier
pauses after the first approval and executes once, after the second."""

import asyncio
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from conftest import FakeIntelligence
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from test_approval_flow import CONFIG, LARGER_ORDER, decide, golden_model, start
from test_case_workflow import MARIA, approve, collect, workflow
from test_case_workflow import start as start_case

from supplyguard.app.case_service import CaseService
from supplyguard.approval.policy import load_approval_policy
from supplyguard.approval.service import DeterministicApprovalService
from supplyguard.calculations.service import DeterministicCostService
from supplyguard.contracts import (
    ApprovalDecision,
    AuthorizationContext,
    ConfirmedIncident,
    SupplierCapability,
)
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


async def _case_context(case_id: str):
    incident = ConfirmedIncident(
        case_id=case_id,
        user_id="u-pm-01",
        supplier_id="S001",
        component_id="MC-17",
        delay_days=12,
        scenario_date=date(2026, 10, 19),
        spending_cap_eur=Decimal("75000"),
        original_message="Atlas Components: 12-day delay on MC-17.",
    )
    cordillera = SupplierCapability(
        supplier_id="S004",
        component_id=incident.component_id,
        lead_time_days=9,
        monthly_capacity_units=3000,
        minimum_order_units=250,
        unit_price_eur=Decimal("9.80"),
        certification_status="valid",
        erp_status="approved",
    )
    result = await FakeIntelligence().investigate(incident)
    return incident, result.impact.model_copy(update={"supplier_capabilities": (cordillera,)})


@pytest.fixture
def real_adapters(monkeypatch: pytest.MonkeyPatch):
    records, store = InMemoryApprovalRecords(), InMemoryExecutionStore()
    monkeypatch.setattr(mcp_server, "_approvals", lambda: records)
    monkeypatch.setattr(mcp_server, "_store", lambda: store)
    approval = DeterministicApprovalService(
        records,
        load_approval_policy(POLICY),
        case_context=_case_context,
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


def test_refused_edit_leaves_original_request_approvable(
    scripted_model, make_services, real_adapters
) -> None:
    approval, execution, store = real_adapters
    services = make_services(approval=approval, execution=execution)
    graph = build_graph(
        model=golden_model(scripted_model), services=services, checkpointer=InMemorySaver()
    )
    request = start(graph)
    first = _approve(graph, request, "u-pm-01", "procurement_manager")
    assert "__interrupt__" in first
    change = LARGER_ORDER.model_copy(update={"payload": {**LARGER_ORDER.payload, "qty": 2000}})

    result = decide(
        graph,
        request,
        approved=False,
        modified_actions=[change.model_dump(mode="json")],
        approver_user_ids=["u-pm-01"],
        approver_roles=["procurement_manager"],
    )

    assert result["__interrupt__"][0].value["request"] == request
    assert "already decided" in result["messages"][-1].content
    assert asyncio.run(approval.records.latest_version(request["case_id"])) == 1
    assert store.results == {}
    finished = _approve(graph, request, "u-fc-01", "finance_controller")
    assert finished["status"] == "completed"
    assert len(store.results) == 1


def test_finance_can_submit_second_approval_without_case_access(
    scripted_model, make_services, real_adapters
) -> None:
    approval, execution, store = real_adapters
    flow = workflow(scripted_model, make_services(approval=approval, execution=execution))
    request = start_case(flow)[-1].payload["request"]
    assert approve(flow, request)[-1].stage == "await_approval"

    class RequestSource:
        async def get_request(self, approval_request_id):
            saved = await approval.records.load(approval_request_id)
            return saved[0] if saved else None

        async def list_open_for(self, role, user_id):
            return ()

        async def has_decided(self, approval_request_id, user_id):
            return False

    finance = AuthorizationContext(user_id="u-fc-01", role="finance_controller")
    decision = ApprovalDecision(
        approval_request_id=request["approval_request_id"],
        case_id=request["case_id"],
        payload_hash=request["payload_hash"],
        approved=True,
        approver_user_ids=(finance.user_id,),
        approver_roles=(finance.role,),
        decided_at=datetime(2026, 10, 19, 12, tzinfo=UTC),
    )

    async def submit():
        app = CaseService(flow, approvals=RequestSource())
        stream = await app.submit_approval(decision, finance)
        return [event async for event in stream]

    events = asyncio.run(submit())
    assert [event.stage for event in events] == ["record_decision"]
    assert all(not event.payload for event in events)
    assert len(store.results) == 1
    assert "Incident report" in collect(flow.resume_case(request["case_id"], MARIA))[-1].message
    with pytest.raises(PermissionError):
        collect(flow.resume_case(request["case_id"], finance))
