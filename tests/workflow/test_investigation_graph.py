"""Investigation after intake: impact, evidence, plan, and what happens when a service fails."""

import asyncio

from langgraph.checkpoint.memory import InMemorySaver

from supplyguard.workflow.graph import build_graph
from supplyguard.workflow.intake.extraction import IncidentExtraction

AUTHORIZATION = {
    "user_id": "u-pm-01",
    "role": "procurement_manager",
    "allowed_scopes": ["internal", "procurement", "public"],
}
GOLDEN = IncidentExtraction(
    supplier_query="Atlas Components", component_query="MC-17", delay_days=12
)


def run_golden(scripted_model, services) -> dict:
    graph = build_graph(
        model=scripted_model(GOLDEN), services=services, checkpointer=InMemorySaver()
    )
    first = {
        "case_id": "CASE-1",
        "user_id": "u-pm-01",
        "authorization": AUTHORIZATION,
        "messages": [("user", "Atlas Components: 12-day delay on MC-17.")],
    }
    return asyncio.run(graph.ainvoke(first, {"configurable": {"thread_id": "CASE-1"}}))


def test_golden_case_reaches_a_recommendation(scripted_model, make_services) -> None:
    services = make_services()

    result = run_golden(scripted_model, services)

    assert result["intelligence"]["impact"]["projected_shortfall_units"] == 5350
    assert result["plan"]["recommended_option_id"] == "OPT-1"
    assert result["status"] == "awaiting_approval"
    assert result["__interrupt__"][0].value["type"] == "approval"
    assert not result.get("errors")
    assert services.intelligence.calls == 1


def test_user_authorization_reaches_evidence_unchanged(scripted_model, make_services) -> None:
    services = make_services()

    run_golden(scripted_model, services)

    assert services.evidence.authorization.user_id == "u-pm-01"
    assert services.evidence.authorization.allowed_scopes == ("internal", "procurement", "public")


def test_failed_impact_analysis_stops_the_case(
    scripted_model, make_services, failing_service
) -> None:
    result = run_golden(scripted_model, make_services(intelligence=failing_service))

    assert result["status"] == "failed"
    assert "plan" not in result
    assert "Impact analysis failed: database unavailable" in result["messages"][-1].content


def test_failed_evidence_is_reported_but_planning_continues(
    scripted_model, make_services, failing_service
) -> None:
    result = run_golden(scripted_model, make_services(evidence=failing_service))

    assert result["plan"]["recommended_option_id"] == "OPT-1"
    assert result["errors"] == ["Evidence retrieval failed: vector store unavailable"]


def test_blocking_challenge_finding_stops_before_approval(
    scripted_model, make_services, blocked_planning
) -> None:
    result = run_golden(scripted_model, make_services(planning=blocked_planning))

    assert result["status"] == "failed"
    assert "Fewer than three strategies were compared." in result["messages"][-1].content
