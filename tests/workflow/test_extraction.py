"""Extraction with a scripted model, and building the shared ConfirmedIncident."""

import asyncio
from decimal import Decimal

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableLambda

from supplyguard.contracts import ComponentCandidate, SpendingCapSource, SupplierCandidate
from supplyguard.workflow.intake.extraction import (
    IncidentExtraction,
    confirm_incident,
    extract,
    transcript,
)

ATLAS = SupplierCandidate(supplier_id="S001", name="Atlas Components")
MC17 = ComponentCandidate(component_id="MC-17", description="Motor controller board 48V")


class ScriptedModel:
    """Returns a fixed extraction and remembers the prompt it was given."""

    def __init__(self, result: IncidentExtraction) -> None:
        self.result = result
        self.prompt = None

    def with_structured_output(self, schema, **kwargs):
        def answer(prompt):
            self.prompt = prompt
            return self.result

        return RunnableLambda(answer)


def test_extract_sends_the_whole_conversation() -> None:
    model = ScriptedModel(IncidentExtraction(supplier_query="Atlas Components"))
    messages = [
        HumanMessage("Atlas is late"),
        AIMessage("Which supplier?"),
        HumanMessage("Atlas Components"),
    ]

    result = asyncio.run(extract(model, messages))

    assert result.supplier_query == "Atlas Components"
    assert "User: Atlas is late\nAssistant: Which supplier?\nUser: Atlas Components" in (
        model.prompt[-1].content
    )


def test_transcript_labels_speakers() -> None:
    assert transcript([HumanMessage("hi"), AIMessage("hello")]) == "User: hi\nAssistant: hello"


def test_confirm_with_stated_cap() -> None:
    extraction = IncidentExtraction(
        supplier_query="Atlas Components",
        component_query="MC-17",
        delay_days=12,
        spending_cap_eur=75000,
    )

    incident = confirm_incident(
        case_id="CASE-1",
        user_id="u-pm-01",
        original_message="Atlas ... MC-17 ...",
        extraction=extraction,
        supplier=ATLAS,
        component=MC17,
    )

    assert (incident.supplier_id, incident.component_id, incident.delay_days) == (
        "S001",
        "MC-17",
        12,
    )
    assert incident.spending_cap_eur == Decimal("75000.0")
    assert incident.spending_cap_source is SpendingCapSource.MESSAGE
    assert str(incident.scenario_date) == "2026-10-19"


def test_missing_cap_uses_policy_default() -> None:
    extraction = IncidentExtraction(supplier_query="S001", component_query="MC-17", delay_days=3)

    incident = confirm_incident(
        case_id="CASE-2",
        user_id="u-pm-01",
        original_message="...",
        extraction=extraction,
        supplier=ATLAS,
        component=MC17,
    )

    assert incident.spending_cap_eur == Decimal("75000")
    assert incident.spending_cap_source is SpendingCapSource.POLICY_DEFAULT
