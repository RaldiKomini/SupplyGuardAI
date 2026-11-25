"""Approval and execution: approve, reject, modify, stale decisions, and restart."""

import asyncio
from datetime import UTC, datetime
from decimal import Decimal

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from supplyguard.contracts import RecoveryAction, RecoveryActionType
from supplyguard.workflow.graph import build_graph
from supplyguard.workflow.intake.extraction import IncidentExtraction

AUTHORIZATION = {
    "user_id": "u-pm-01",
    "role": "procurement_manager",
    "allowed_scopes": ["internal", "procurement", "public"],
}
CONFIG = {"configurable": {"thread_id": "CASE-1"}}
LARGER_ORDER = RecoveryAction(
    action_id="ACT-1",
    action_type=RecoveryActionType.PURCHASE_ALTERNATIVE,
    payload={"supplier_id": "S004", "component_id": "MC-17", "qty": 4000},
    cost_eur=Decimal("39200"),
)


def golden_model(scripted_model):
    return scripted_model(
        IncidentExtraction(
            supplier_query="Atlas Components", component_query="MC-17", delay_days=12
        )
    )


def start(graph) -> dict:
    """Runs the golden case until it pauses for approval; returns the approval request."""
    first = {
        "case_id": "CASE-1",
        "user_id": "u-pm-01",
        "authorization": AUTHORIZATION,
        "messages": [("user", "Atlas Components: 12-day delay on MC-17.")],
    }
    result = asyncio.run(graph.ainvoke(first, CONFIG))
    pause = result["__interrupt__"][0].value
    assert pause["type"] == "approval"
    return pause["request"]


def decide(graph, request: dict, *, approved: bool, **extra) -> dict:
    decision = {
        "approval_request_id": request["approval_request_id"],
        "case_id": request["case_id"],
        "payload_hash": request["payload_hash"],
        "approved": approved,
        "approver_user_ids": ["u-pm-01"] if approved else [],
        "approver_roles": ["procurement_manager"] if approved else [],
        "decided_at": datetime(2026, 10, 19, 12, tzinfo=UTC).isoformat(),
        **extra,
    }
    return asyncio.run(graph.ainvoke(Command(resume=decision), CONFIG))


def test_nothing_is_executed_before_approval(scripted_model, make_services) -> None:
    services = make_services()
    graph = build_graph(
        model=golden_model(scripted_model), services=services, checkpointer=InMemorySaver()
    )

    request = start(graph)

    assert request["payload_version"] == 1
    assert services.execution.writes == []
    assert asyncio.run(graph.aget_state(CONFIG)).next == ("await_approval",)


def test_approval_executes_once_and_completes(scripted_model, make_services) -> None:
    services = make_services()
    graph = build_graph(
        model=golden_model(scripted_model), services=services, checkpointer=InMemorySaver()
    )

    result = decide(graph, start(graph), approved=True)

    assert result["status"] == "completed"
    assert len(services.execution.writes) == 1
    assert len(services.approval.recorded) == 1
    assert "carried out" in result["messages"][-1].content


def test_rejection_executes_nothing(scripted_model, make_services) -> None:
    services = make_services()
    graph = build_graph(
        model=golden_model(scripted_model), services=services, checkpointer=InMemorySaver()
    )

    result = decide(graph, start(graph), approved=False, reason="Wait for Atlas.")

    assert result["status"] == "completed"
    assert services.execution.writes == []
    assert "rejected" in result["messages"][-1].content


def test_modification_needs_a_new_approval(scripted_model, make_services) -> None:
    services = make_services()
    graph = build_graph(
        model=golden_model(scripted_model), services=services, checkpointer=InMemorySaver()
    )
    first = start(graph)

    result = decide(
        graph, first, approved=False, modified_actions=[LARGER_ORDER.model_dump(mode="json")]
    )
    second = result["__interrupt__"][0].value["request"]
    assert second["payload_version"] == 2
    assert second["payload_hash"] != first["payload_hash"]
    assert services.execution.writes == []  # the modification itself executed nothing

    result = decide(graph, second, approved=True)
    assert services.execution.writes == [LARGER_ORDER.payload]  # the modified quantity


def test_decision_for_an_old_request_is_refused(scripted_model, make_services) -> None:
    services = make_services()
    graph = build_graph(
        model=golden_model(scripted_model), services=services, checkpointer=InMemorySaver()
    )
    first = start(graph)
    decide(graph, first, approved=False, modified_actions=[LARGER_ORDER.model_dump(mode="json")])

    result = decide(graph, first, approved=True)  # approving version 1 after it was modified

    assert "__interrupt__" in result  # still waiting
    assert "different approval request" in result["messages"][-1].content
    assert services.execution.writes == []


def test_decision_refused_by_approval_rules_keeps_waiting(
    scripted_model, make_services, refusing_approval
) -> None:
    services = make_services(approval=refusing_approval)
    graph = build_graph(
        model=golden_model(scripted_model), services=services, checkpointer=InMemorySaver()
    )

    result = decide(graph, start(graph), approved=True)

    assert "__interrupt__" in result
    assert "finance_controller" in result["messages"][-1].content
    assert services.execution.writes == []


def test_restart_while_waiting_keeps_the_same_payload_and_executes_once(
    scripted_model, make_services
) -> None:
    saver, services = InMemorySaver(), make_services()
    request = start(
        build_graph(model=golden_model(scripted_model), services=services, checkpointer=saver)
    )

    restarted = build_graph(
        model=scripted_model(), services=services, checkpointer=saver
    )  # new process
    state = asyncio.run(restarted.aget_state(CONFIG))
    assert state.next == ("await_approval",)
    assert state.values["approval_request"]["payload_hash"] == request["payload_hash"]

    decide(restarted, request, approved=True)
    assert len(services.execution.writes) == 1
    assert services.intelligence.calls == 1  # finished steps were not repeated


def test_invalid_edit_returns_to_approval_with_the_reason(
    scripted_model, make_services, capacity_limited_approval
) -> None:
    """Asking for more than Cordillera can supply must not leave the case stuck."""
    services = make_services(approval=capacity_limited_approval)
    graph = build_graph(
        model=golden_model(scripted_model), services=services, checkpointer=InMemorySaver()
    )
    first = start(graph)
    too_much = LARGER_ORDER.model_copy(update={"payload": {**LARGER_ORDER.payload, "qty": 9000}})

    result = decide(
        graph, first, approved=False, modified_actions=[too_much.model_dump(mode="json")]
    )

    assert (
        "could not be accepted: Cordillera can supply at most 3000"
        in result["messages"][-1].content
    )
    still = result["__interrupt__"][0].value["request"]
    assert still["payload_hash"] == first["payload_hash"]  # the current request still stands
    assert asyncio.run(graph.aget_state(CONFIG)).next == ("await_approval",)

    result = decide(graph, still, approved=True)  # the user can still decide
    assert result["status"] == "completed"
    assert services.execution.writes == [
        {"supplier_id": "S004", "component_id": "MC-17", "qty": 3000}
    ]


def test_unconfirmed_stock_stops_before_any_approval_request(
    scripted_model, make_services, unconfirmed_stock
) -> None:
    services = make_services(intelligence=unconfirmed_stock)
    graph = build_graph(
        model=golden_model(scripted_model), services=services, checkpointer=InMemorySaver()
    )
    first = {
        "case_id": "CASE-1",
        "user_id": "u-pm-01",
        "authorization": AUTHORIZATION,
        "messages": [("user", "Atlas Components: 12-day delay on MC-17.")],
    }

    result = asyncio.run(graph.ainvoke(first, CONFIG))

    assert "__interrupt__" not in result
    assert result["status"] == "failed"
    assert "Stock at W-KAT is inconsistent and was excluded" in result["messages"][-1].content
    assert services.approval.requests_created == 0
    assert services.execution.writes == []
