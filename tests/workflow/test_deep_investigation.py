"""The Deep Agent: approved read-only tools, sourced findings, delegation, and its place in
the workflow. A scripted tool-calling model drives the real deepagents runtime."""

import asyncio
import json
from decimal import Decimal

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.utils.function_calling import convert_to_openai_tool
from langgraph.checkpoint.memory import InMemorySaver

from supplyguard.agents.deep_investigation import (
    InvestigationFinding,
    InvestigationIncomplete,
    investigate_case,
)
from supplyguard.agents.deep_investigation.agent import (
    MAX_FINDINGS,
    _tools,
    certification_contradictions,
)
from supplyguard.contracts import (
    CoveragePosition,
    DependencyReport,
    EvidenceBundle,
    EvidenceItem,
    ImpactReport,
    OperationalIntelligenceResult,
    SourceReference,
)
from supplyguard.workflow.graph import build_graph
from supplyguard.workflow.intake.extraction import IncidentExtraction

INTELLIGENCE = OperationalIntelligenceResult(
    impact=ImpactReport(
        case_id="CASE-1",
        component_id="MC-17",
        inventory=(),
        total_usable_units=3200,
        daily_demand_units=Decimal("450"),
        projected_shortfall_units=5350,
        affected_product_ids=("P012",),
        affected_factory_ids=("F2",),
        at_risk_orders=(),
        revenue_at_risk_eur=Decimal("0"),
        sources=(SourceReference(source_id="inventory.csv", source_type="csv"),),
    ),
    dependencies=DependencyReport(case_id="CASE-1", findings=()),
)
EVIDENCE = EvidenceBundle(
    case_id="CASE-1",
    query="MC-17",
    items=(
        EvidenceItem(
            evidence_id="E1",
            title="Helios capacity e-mail",
            document_type="email",
            content="Helios can only ship 1,500 units per month.",
            source=SourceReference(source_id="DOC-EML-HELIOS", source_type="document"),
        ),
    ),
)


def call(name: str, args: dict, n: int) -> AIMessage:
    return AIMessage("", tool_calls=[{"name": name, "args": args, "id": f"call-{n}"}])


class ScriptedToolModel(BaseChatModel):
    """Answers with the scripted messages in order, then 'Done.'; remembers the tool names."""

    script: list = []
    bound_tools: list = []

    @property
    def _llm_type(self) -> str:
        return "scripted-tools"

    def bind_tools(self, tools, **kwargs):
        self.bound_tools.append({convert_to_openai_tool(t)["function"]["name"] for t in tools})
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        message = self.script.pop(0) if self.script else AIMessage("Done.")
        return ChatResult(generations=[ChatGeneration(message=message)])


def run(model) -> list[InvestigationFinding]:
    return asyncio.run(investigate_case(model, INTELLIGENCE, EVIDENCE))


def test_findings_are_recorded_with_their_sources() -> None:
    model = ScriptedToolModel(
        script=[
            call("impact_summary", {}, 1),
            call(
                "record_finding",
                {
                    "finding": "Katowice runs out first.",
                    "severity": "warning",
                    "source_ids": ["inventory.csv"],
                },
                2,
            ),
        ]
    )

    findings = run(model)

    assert findings == [
        InvestigationFinding(
            finding="Katowice runs out first.", severity="warning", source_ids=["inventory.csv"]
        )
    ]


def test_a_finding_without_a_known_source_is_not_recorded() -> None:
    model = ScriptedToolModel(
        script=[
            call(
                "record_finding",
                {
                    "finding": "Buy from Borealis.",
                    "severity": "info",
                    "source_ids": ["made-up-source"],
                },
                1,
            ),
        ]
    )

    assert run(model) == []


def test_only_approved_tools_and_no_write_tools() -> None:
    model = ScriptedToolModel(script=[])

    run(model)

    tools = model.bound_tools[0]
    assert {
        "impact_summary",
        "supplier_options",
        "dependency_findings",
        "evidence_items",
        "record_finding",
    } <= tools
    assert {"write_todos", "task"} <= tools  # planning and delegation
    assert not any(
        word in name
        for name in tools
        for word in ("purchase", "procure", "approve", "execute", "transfer")
    )


def test_evidence_checks_are_delegated_to_the_sub_agent() -> None:
    model = ScriptedToolModel(
        script=[
            call(
                "task",
                {
                    "subagent_type": "evidence-checker",
                    "description": "Check Helios capacity documents.",
                },
                1,
            ),
            call("evidence_items", {"topic": "Helios capacity"}, 2),
            call(
                "record_finding",
                {
                    "finding": "Helios e-mail says 1,500 units per month.",
                    "severity": "warning",
                    "source_ids": ["DOC-EML-HELIOS"],
                },
                3,
            ),
        ]
    )

    findings = run(model)

    assert [f.source_ids for f in findings] == [["DOC-EML-HELIOS"]]


def test_findings_reach_the_report_and_a_failure_does_not_stop_the_case(
    scripted_model, make_services, quick_investigation
) -> None:
    quick_investigation.findings = [
        InvestigationFinding(
            finding="Delta shares upstream Veltro with Atlas.",
            severity="warning",
            source_ids=["graph:SOURCES_FROM"],
        )
    ]
    golden = IncidentExtraction(
        supplier_query="Atlas Components", component_query="MC-17", delay_days=12
    )
    graph = build_graph(
        model=scripted_model(golden, golden),  # one extraction per case
        services=make_services(),
        checkpointer=InMemorySaver(),
    )
    first = {
        "case_id": "CASE-1",
        "user_id": "u-pm-01",
        "authorization": {"user_id": "u-pm-01", "role": "procurement_manager"},
        "messages": [("user", "Atlas Components: 12-day delay on MC-17.")],
    }

    result = asyncio.run(graph.ainvoke(first, {"configurable": {"thread_id": "CASE-1"}}))
    assert result["investigation_findings"][0]["finding"].startswith("Delta shares upstream")

    quick_investigation.error = RuntimeError("model unavailable")
    first["case_id"] = "CASE-2"
    result = asyncio.run(graph.ainvoke(first, {"configurable": {"thread_id": "CASE-2"}}))
    assert "Deep investigation failed: model unavailable" in result["errors"]
    assert result["__interrupt__"][0].value["type"] == "approval"  # the case continued


def test_step_limit_keeps_the_findings_recorded_so_far(monkeypatch) -> None:
    monkeypatch.setattr("supplyguard.agents.deep_investigation.agent.MAX_AGENT_STEPS", 12)
    finding = {
        "finding": "Shortfall is 5,350 units.",
        "severity": "warning",
        "source_ids": ["inventory.csv"],
    }
    model = ScriptedToolModel(
        script=[call("record_finding", finding, 0)]
        + [call("impact_summary", {}, n) for n in range(1, 30)]
    )

    with pytest.raises(InvestigationIncomplete) as stopped:
        run(model)

    assert [f.finding for f in stopped.value.findings] == ["Shortfall is 5,350 units."]


def test_the_agent_cannot_mark_a_finding_blocking() -> None:
    """Blocking decisions come from deterministic checks, never from the model."""
    model = ScriptedToolModel(
        script=[
            call(
                "record_finding",
                {
                    "finding": "Stop everything.",
                    "severity": "blocking",
                    "source_ids": ["inventory.csv"],
                },
                1,
            )
        ]
    )

    assert run(model) == []


def test_findings_are_capped_so_the_report_stays_readable() -> None:
    finding = {"severity": "info", "source_ids": ["inventory.csv"]}
    model = ScriptedToolModel(
        script=[call("record_finding", {**finding, "finding": f"Note {n}."}, n) for n in range(8)]
    )

    assert len(run(model)) == MAX_FINDINGS


def test_the_hub_is_not_presented_as_a_factory() -> None:
    hub = CoveragePosition(location_id="W-HUB", usable_units=800, daily_demand_units=Decimal("0"))
    plant = CoveragePosition(
        factory_id="F2", location_id="W-KAT", usable_units=600, daily_demand_units=Decimal("150")
    )
    intelligence = INTELLIGENCE.model_copy(
        update={"impact": INTELLIGENCE.impact.model_copy(update={"coverage": (plant, hub)})}
    )
    impact_summary = next(
        t for t in _tools(intelligence, EVIDENCE, []) if t.name == "impact_summary"
    )

    kinds = {c["location_id"]: c["kind"] for c in json.loads(impact_summary.invoke({}))["coverage"]}

    assert kinds == {"W-KAT": "factory warehouse", "W-HUB": "hub (transfer stock)"}


def test_a_wrong_expired_certification_claim_is_refused() -> None:
    certification = {"S001": "valid", "S003": "expired"}

    assert certification_contradictions(
        "Atlas Components (S001) has a current contract, but its certification is expired.",
        certification,
    ) == ["S001"]
    assert (
        certification_contradictions(
            "S003 has an expired certification and S002 has a 21-day lead time.", certification
        )
        == []
    )
