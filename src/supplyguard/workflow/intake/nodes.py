"""Intake graph steps: read the incident, ask what is unclear, or stop after too many questions."""

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.types import interrupt

from supplyguard.contracts import CaseStatus, ComponentCandidate, SupplierCandidate
from supplyguard.contracts.protocols import ContentSafetyService, MasterDataService
from supplyguard.workflow.intake.extraction import IncidentExtraction, confirm_incident, extract
from supplyguard.workflow.intake.rules import (
    DELAY_QUESTION,
    MAX_CLARIFICATION_ROUNDS,
    clarification_message,
    component_question,
    link_question,
    pick_component,
    pick_supplier,
    supplier_question,
)
from supplyguard.workflow.state import CaseState


async def resolve(
    extraction: IncidentExtraction, master_data: MasterDataService
) -> tuple[SupplierCandidate | None, ComponentCandidate | None, list[str]]:
    """The certain supplier and component, and the questions for everything that is not."""
    component = None
    component_ask = None
    if extraction.component_query:
        options = await master_data.find_components(extraction.component_query)
        component = pick_component(extraction.component_query, options)
        if component is None:
            component_ask = component_question(extraction.component_query, options)
    else:
        component_ask = component_question(None, ())

    supplier = None
    supplier_ask = None
    if extraction.supplier_query:
        options = await master_data.find_suppliers(extraction.supplier_query)
        supplier = pick_supplier(extraction.supplier_query, options)
        if supplier is None:
            supplier_ask = supplier_question(extraction.supplier_query, options)
    else:
        options = await master_data.suppliers_of(component.component_id) if component else ()
        supplier_ask = supplier_question(None, options)

    if supplier and component:
        linked = await master_data.suppliers_of(component.component_id)
        if supplier.supplier_id not in {s.supplier_id for s in linked}:
            supplier_ask = link_question(supplier, component, linked)
            supplier = None  # not verified, so not usable

    delay_ask = DELAY_QUESTION if extraction.delay_days is None else None
    questions = [q for q in (supplier_ask, component_ask, delay_ask) if q]
    return supplier, component, questions


def make_read_incident(
    model: BaseChatModel, master_data: MasterDataService, content_safety: ContentSafetyService
):
    async def read_incident(state: CaseState) -> dict:
        history = []
        replacements = []
        findings = []
        for message in state["messages"]:
            if isinstance(message, HumanMessage):
                clean, message_findings = content_safety.sanitize(
                    str(message.content), "user_message"
                )
                findings.extend(f.model_dump(mode="json") for f in message_findings)
                if clean != message.content:
                    message = message.model_copy(update={"content": clean})
                    replacements.append(message)
            history.append(message)
        found = {"security_findings": findings, "messages": replacements}

        extraction = await extract(model, history)
        supplier, component, questions = await resolve(extraction, master_data)
        if questions:
            return {"questions": questions, "status": CaseStatus.AWAITING_INPUT.value, **found}

        first_message = next(m for m in history if isinstance(m, HumanMessage))
        incident = confirm_incident(
            case_id=state["case_id"],
            user_id=state["user_id"],
            original_message=str(first_message.content),
            extraction=extraction,
            supplier=supplier,
            component=component,
        )
        return {
            "incident": incident.model_dump(mode="json"),
            "questions": [],
            "status": CaseStatus.IN_PROGRESS.value,
            **found,
        }

    return read_incident


def route_after_read(state: CaseState) -> str:
    if state.get("incident"):
        return "confirmed"
    if state.get("clarification_rounds", 0) < MAX_CLARIFICATION_ROUNDS:
        return "ask_clarification"
    return "stop_intake"


def ask_clarification(state: CaseState) -> dict:
    """Pauses the case. On resume LangGraph runs this step again from the top, so nothing
    before interrupt() may have side effects."""
    question = clarification_message(state["questions"])
    answer = interrupt({"type": "clarification", "question": question})
    return {
        "messages": [AIMessage(question), HumanMessage(str(answer))],
        "clarification_rounds": state.get("clarification_rounds", 0) + 1,
    }


def stop_intake(state: CaseState) -> dict:
    """Too many questions: stop asking. The user's next message starts intake again."""
    text = "I can't start the investigation without this information. " + clarification_message(
        state["questions"]
    )
    return {"messages": [AIMessage(text)], "clarification_rounds": 0}
