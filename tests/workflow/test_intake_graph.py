"""The intake part of the graph: confirm, ask and resume, or stop after two questions."""

import asyncio

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from supplyguard.workflow.graph import build_graph
from supplyguard.workflow.intake.extraction import IncidentExtraction
from supplyguard.workflow.intake.nodes import resolve

GOLDEN = IncidentExtraction(
    supplier_query="Atlas Components",
    component_query="MC-17",
    delay_days=12,
    spending_cap_eur=75000,
)


def start(graph, message: str, case_id: str = "CASE-1") -> tuple[dict, dict]:
    config = {"configurable": {"thread_id": case_id}}
    first = {"case_id": case_id, "user_id": "u-pm-01", "messages": [("user", message)]}
    return config, asyncio.run(graph.ainvoke(first, config))


def test_clear_message_is_confirmed_without_questions(master_data, scripted_model) -> None:
    graph = build_graph(
        model=scripted_model(GOLDEN), master_data=master_data, checkpointer=InMemorySaver()
    )

    _, result = start(graph, "Atlas Components announced a 12-day delay for MC-17 ...")

    assert "__interrupt__" not in result
    assert result["incident"]["supplier_id"] == "S001"
    assert result["incident"]["component_id"] == "MC-17"
    assert result["status"] == "in_progress"


def test_ambiguous_supplier_asks_then_resumes_with_the_answer(master_data, scripted_model) -> None:
    model = scripted_model(
        IncidentExtraction(supplier_query="Atlas", component_query="MC-17", delay_days=12),
        IncidentExtraction(
            supplier_query="Atlas Components", component_query="MC-17", delay_days=12
        ),
    )
    graph = build_graph(model=model, master_data=master_data, checkpointer=InMemorySaver())

    config, result = start(graph, "Atlas called, MC-17 will be 12 days late.")
    pause = result["__interrupt__"][0].value
    assert pause["type"] == "clarification"
    assert "Atlas Components (S001) or Atlas Logistik GmbH (S006)" in pause["question"]

    result = asyncio.run(graph.ainvoke(Command(resume="Atlas Components"), config))
    assert result["incident"]["supplier_id"] == "S001"
    assert any(m.content == "Atlas Components" for m in result["messages"])  # answer recorded


def test_stops_after_two_unanswered_questions(master_data, scripted_model) -> None:
    vague = IncidentExtraction(supplier_query="Atlas")
    graph = build_graph(
        model=scripted_model(vague, vague, vague),
        master_data=master_data,
        checkpointer=InMemorySaver(),
    )

    config, _ = start(graph, "Atlas says the motor board is late.")
    asyncio.run(graph.ainvoke(Command(resume="not sure"), config))
    result = asyncio.run(graph.ainvoke(Command(resume="no idea"), config))

    assert "__interrupt__" not in result
    assert result["status"] == "awaiting_input"
    assert result["messages"][-1].content.startswith("I can't start the investigation")


def test_unknown_supplier_is_asked_with_the_component_suppliers(master_data) -> None:
    extraction = IncidentExtraction(component_query="MC-17", delay_days=12)

    supplier, component, questions = asyncio.run(resolve(extraction, master_data))

    assert supplier is None and component.component_id == "MC-17"
    assert questions[0].startswith("Which supplier is affected? This component is supplied by")
