"""Investigation steps: impact (Raldi), evidence (George), the Deep Agent investigation,
and the recovery plan (Athanasia)."""

import re

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage

from supplyguard.agents.deep_investigation import InvestigationIncomplete, investigate_case
from supplyguard.contracts import (
    AuthorizationContext,
    CaseStatus,
    ConfirmedIncident,
    EvidenceBundle,
    OperationalIntelligenceResult,
    RecoveryPlan,
    Severity,
)
from supplyguard.workflow.services import WorkflowServices
from supplyguard.workflow.state import CaseState


def make_investigation_steps(services: WorkflowServices, model: BaseChatModel) -> dict:
    async def investigate_impact(state: CaseState) -> dict:
        incident = ConfirmedIncident.model_validate(state["incident"])
        baseline_days, alternatives = _delay_scenarios(incident, state.get("evidence"))
        incident = incident.model_copy(update={"delay_days": baseline_days})
        try:
            intelligence = await services.intelligence.investigate(incident)
        except Exception as error:  # without impact numbers nothing can be planned
            return {"intelligence": None, "errors": [f"Impact analysis failed: {error}"]}
        scenarios = []
        for delay_days in alternatives:
            try:
                result = await services.intelligence.investigate(
                    incident.model_copy(update={"delay_days": delay_days})
                )
                shortfall = result.impact.projected_shortfall_units
            except Exception:
                shortfall = None
            scenarios.append({"delay_days": delay_days, "projected_shortfall_units": shortfall})
        return {
            "incident": incident.model_dump(mode="json"),
            "intelligence": intelligence.model_dump(mode="json"),
            "delay_sensitivities": scenarios,
        }

    async def collect_evidence(state: CaseState) -> dict:
        incident = ConfirmedIncident.model_validate(state["incident"])
        authorization = AuthorizationContext.model_validate(state["authorization"])
        try:
            evidence = await services.evidence.retrieve(incident, authorization)
        except Exception as error:  # planning can continue; the missing evidence is reported
            empty = EvidenceBundle(case_id=incident.case_id, query="", items=())
            return {
                "evidence": empty.model_dump(mode="json"),
                "errors": [f"Evidence retrieval failed: {error}"],
            }
        return {"evidence": evidence.model_dump(mode="json")}

    async def deep_investigation(state: CaseState) -> dict:
        """Extended investigation. It adds findings; planning does not depend on it, so a
        failure is recorded and the case continues."""
        try:
            findings = await investigate_case(
                model,
                OperationalIntelligenceResult.model_validate(state["intelligence"]),
                EvidenceBundle.model_validate(state["evidence"]),
            )
        except InvestigationIncomplete as incomplete:  # keep what was found before the limit
            return {
                "investigation_findings": [f.model_dump() for f in incomplete.findings],
                "errors": [f"Deep investigation incomplete: {incomplete}"],
            }
        except Exception as error:
            return {"investigation_findings": [], "errors": [f"Deep investigation failed: {error}"]}
        return {"investigation_findings": [f.model_dump() for f in findings]}

    async def plan_recovery(state: CaseState) -> dict:
        try:
            plan = await services.planning.propose(
                ConfirmedIncident.model_validate(state["incident"]),
                OperationalIntelligenceResult.model_validate(state["intelligence"]),
                EvidenceBundle.model_validate(state["evidence"]),
            )
        except Exception as error:
            return {"plan": None, "errors": [f"Recovery planning failed: {error}"]}
        return {"plan": plan.model_dump(mode="json")}

    return {
        "investigate_impact": investigate_impact,
        "collect_evidence": collect_evidence,
        "deep_investigation": deep_investigation,
        "plan_recovery": plan_recovery,
    }


def route_after_impact(state: CaseState) -> str:
    return "deep_investigation" if state.get("intelligence") else "stop_case"


def _delay_scenarios(incident: ConfirmedIncident, evidence_payload: dict | None):
    if not evidence_payload:
        return incident.delay_days, ()
    evidence = EvidenceBundle.model_validate(evidence_payload)
    items = {item.evidence_id: item for item in evidence.items}
    component = re.compile(rf"(?<![\w-]){re.escape(incident.component_id)}(?![\w-])", re.I)
    for conflict in evidence.conflicts:
        notice = items.get(conflict.selected_evidence_id)
        if (
            conflict.subject == f"{incident.supplier_id}.delay_days"
            and type(conflict.planning_value) is int
            and conflict.planning_value >= 0
            and notice is not None
            and notice.document_type == "supplier_notice"
            and notice.status != "superseded"
            and component.search(notice.content)
        ):
            baseline_days = conflict.planning_value
            alternatives = {
                value
                for value in (*conflict.sensitivity_values, incident.delay_days)
                if type(value) is int and value >= 0 and value != baseline_days
            }
            return baseline_days, tuple(sorted(alternatives))
    return incident.delay_days, ()


def blocking_warnings(state: CaseState) -> list[str]:
    """Blocking data-quality warnings from the impact analysis, e.g. unconfirmed stock."""
    if not state.get("intelligence"):
        return []
    result = OperationalIntelligenceResult.model_validate(state["intelligence"])
    warnings = [*result.warnings, *result.impact.warnings, *result.dependencies.warnings]
    messages = [w.message for w in warnings if w.severity == Severity.BLOCKING]
    return list(dict.fromkeys(messages))  # the same warning may appear in two places


def route_after_plan(state: CaseState) -> str:
    if not state.get("plan") or blocking_warnings(state):
        return "stop_case"  # never ask for approval on unconfirmed numbers
    plan = RecoveryPlan.model_validate(state["plan"])
    if plan.recommended_option_id is None or plan.needs_revision:
        return "stop_case"
    return "ready_for_approval"


def stop_case(state: CaseState) -> dict:
    """End the case without a recommendation and say why."""
    reasons = list(state.get("errors") or []) + blocking_warnings(state)
    if state.get("plan"):
        plan = RecoveryPlan.model_validate(state["plan"])
        reasons += [f.message for f in plan.challenge_findings if f.severity == "blocking"]
        if plan.recommended_option_id is None:
            reasons.append(plan.recommendation_reason)
    text = "I could not prepare a recommendation for approval:\n" + "\n".join(
        f"- {r}" for r in reasons
    )
    return {"messages": [AIMessage(text)], "status": CaseStatus.FAILED.value}
