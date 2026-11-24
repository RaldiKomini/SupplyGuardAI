"""Extraction with a scripted model, and building the shared ConfirmedIncident."""

import asyncio
from decimal import Decimal

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableLambda
from pydantic import ValidationError

from supplyguard.contracts import (
    ComponentCandidate,
    ConfirmedIncident,
    SpendingCapSource,
    SupplierCandidate,
)
from supplyguard.workflow.intake.extraction import (
    IncidentExtraction,
    confirm_incident,
    extract,
    spending_limit,
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


def test_message_cannot_turn_off_purchase_approval() -> None:
    """Approval is a code rule: neither the model's output nor the contract has a switch for it."""
    assert "purchase_order_needs_approval" not in IncidentExtraction.model_fields
    with pytest.raises(ValidationError):
        IncidentExtraction(purchase_order_needs_approval=False)
    assert "purchase_order_requires_approval" not in ConfirmedIncident.model_fields


def test_user_can_lower_the_spending_cap() -> None:
    assert spending_limit(50000) == (Decimal("50000"), SpendingCapSource.MESSAGE, None)


def test_user_cannot_raise_the_spending_cap() -> None:
    """Above EUR 75,000 needs Finance Director approval: keep the request, plan with the cap."""
    cap, source, requested = spending_limit(90000)

    assert cap == Decimal("75000")
    assert source is SpendingCapSource.POLICY_DEFAULT
    assert requested == Decimal("90000")


def test_no_stated_cap_uses_the_policy_cap() -> None:
    assert spending_limit(None) == (Decimal("75000"), SpendingCapSource.POLICY_DEFAULT, None)


def test_confirmed_incident_keeps_a_higher_request_without_applying_it() -> None:
    extraction = IncidentExtraction(
        supplier_query="Atlas Components",
        component_query="MC-17",
        delay_days=12,
        spending_cap_eur=90000,
    )

    incident = confirm_incident(
        case_id="CASE-3",
        user_id="u-pm-01",
        original_message="... must not exceed EUR 90,000 ...",
        extraction=extraction,
        supplier=ATLAS,
        component=MC17,
    )

    assert incident.spending_cap_eur == Decimal("75000")
    assert incident.requested_spending_cap_eur == Decimal("90000")
