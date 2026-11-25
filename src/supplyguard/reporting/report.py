"""The incident report and the approved resolution summary.

Built from templates over the saved case, not by the model, so every number and source in the
report is one the workflow received from the owning component.
"""

from collections.abc import Sequence
from decimal import Decimal

from supplyguard.contracts import (
    ApprovalDecision,
    ApprovalRequest,
    ConfirmedIncident,
    EvidenceBundle,
    ExecutionResult,
    ExecutionStatus,
    ImpactReport,
    OperationalIntelligenceResult,
    RecoveryPlan,
    ResolutionMemory,
    SecurityFinding,
    Severity,
    SourceReference,
)


def _eur(value) -> str:
    return f"EUR {value:,.0f}"


def _quantity(value) -> str:
    """450.0000 -> "450", 12.50 -> "12.5": no trailing zeros, thousands separated."""
    return f"{Decimal(str(value)).normalize():,f}"


def _sources(sources: tuple[SourceReference, ...]) -> str:
    return "; ".join(dict.fromkeys(s.source_id for s in sources)) or "no source given"


def _bullets(lines: list[str]) -> list[str]:
    return [f"- {line}" for line in lines] or ["- none"]


def _load(state: dict):
    def get(key, model):
        return model.model_validate(state[key]) if state.get(key) else None

    results = [ExecutionResult.model_validate(r) for r in state.get("execution_results") or []]
    return (
        get("incident", ConfirmedIncident),
        get("intelligence", OperationalIntelligenceResult),
        get("evidence", EvidenceBundle),
        get("plan", RecoveryPlan),
        get("approval_request", ApprovalRequest),
        get("decision", ApprovalDecision),
        results,
    )


def completed_action_count(results: Sequence[ExecutionResult]) -> int:
    return sum(
        result.status in (ExecutionStatus.SUCCEEDED, ExecutionStatus.ALREADY_EXECUTED)
        for result in results
    )


def _executed(results: list[ExecutionResult]) -> bool:
    return bool(results) and completed_action_count(results) == len(results)


def late_penalty_summary(impact: ImpactReport) -> str | None:
    traces = [trace for trace in impact.calculations if trace.calculation == "late_penalty_cost"]
    if not traces:
        return None
    total = sum(trace.result for trace in traces)
    return (
        f"Tier-1 late-penalty scenario: EUR {total:,.2f} across {len(traces)} orders "
        "if they ship at delayed replenishment. Each order is counted once (customer_orders.csv)."
    )


def build_incident_report(state: dict) -> str:
    incident, intelligence, evidence, plan, request, decision, results = _load(state)
    out = [f"# Incident report: case {state['case_id']}", ""]

    completed = completed_action_count(results)
    if results and completed == len(results):
        outcome = f"The approved plan was executed ({_eur(request.total_cost_eur)})."
    elif completed:
        outcome = (
            f"{completed} of {len(results)} approved actions completed. See execution outcome."
        )
    elif decision and not decision.approved and not decision.is_modification:
        outcome = "The recommendation was rejected; nothing was executed."
    else:
        outcome = "No action was executed."
    out += [
        "## Summary",
        f"{incident.supplier_name} ({incident.supplier_id}) reported a "
        f"{incident.delay_days}-day {incident.disruption_type.value.replace('_', ' ')} on "
        f"{incident.component_id} ({incident.component_description}). {outcome}",
        "",
    ]

    cap_source = incident.spending_cap_source.value.replace("_", " ")
    cap = f"{_eur(incident.spending_cap_eur)} ({cap_source})"
    facts = [f"Spending limit: {cap}"]
    if incident.requested_spending_cap_eur:
        facts.append(
            f"Requested limit {_eur(incident.requested_spending_cap_eur)} not applied: "
            "above the policy cap it needs Finance Director approval"
        )
    if evidence:
        facts += [
            f"{e.title}: {e.document_type}, {e.source.source_id}"
            f"{f' v{e.version}' if e.version else ''}"
            f"{f', effective {e.effective_date}' if e.effective_date else ''}"
            for e in evidence.items
        ]
    out += ["## Facts and sources", *_bullets(facts), ""]

    if intelligence:
        impact = intelligence.impact
        penalties = late_penalty_summary(impact)
        out += [
            "## Operational impact",
            *_bullets(
                [
                    f"Usable stock: {impact.total_usable_units:,} units; daily demand "
                    f"{_quantity(impact.daily_demand_units)} units",
                    f"Projected shortfall: {impact.projected_shortfall_units:,} units; "
                    "first stockout "
                    f"{impact.first_stockout_date or 'not projected'}",
                    f"Affected products: {', '.join(impact.affected_product_ids)}; factories: "
                    f"{', '.join(impact.affected_factory_ids)}",
                    f"Open customer orders at risk: {len(impact.at_risk_orders)}",
                    "Revenue at risk (exposure, not an expected loss): "
                    f"{_eur(impact.revenue_at_risk_eur)}",
                    f"Sources: {_sources(impact.sources)}",
                    *([penalties] if penalties else []),
                ]
            ),
            "",
        ]

    if sensitivities := state.get("delay_sensitivities"):
        out += ["## Delay sensitivity", ""]
        for scenario in sensitivities:
            shortfall = scenario["projected_shortfall_units"]
            estimate = (
                f"{shortfall:,} units short" if shortfall is not None else "estimate unavailable"
            )
            out.append(f"- {scenario['delay_days']}-day delay: {estimate}.")
        out.append("")

    if evidence:
        out += [
            "## Conflicting evidence",
            *_bullets(
                [
                    f"{c.subject}: {c.description}"
                    f"{f' Resolution: {c.resolution_reason}' if c.resolution_reason else ''}"
                    for c in evidence.conflicts
                ]
            ),
            "",
        ]

    # From the user's own messages and from the retrieved documents.
    security = [SecurityFinding.model_validate(f) for f in state.get("security_findings") or []]
    security += list(evidence.security_findings) if evidence else []
    out += [
        "## Security findings",
        *_bullets(
            [
                f"{f.finding_type}: {f.message} ({'blocked' if f.blocked else 'reported'})"
                for f in security
            ]
        ),
        "",
    ]

    findings = state.get("investigation_findings") or []
    out += [
        "## AI investigation notes (unverified)",
        "Written by the investigation agent from the case data, with their sources. They help "
        "the reviewer but do not change the plan; the numbers above come from the calculations.",
        "",
        *_bullets(
            [f"[{f['severity']}] {f['finding']} ({'; '.join(f['source_ids'])})" for f in findings]
        ),
        "",
    ]

    if plan:
        rows = [
            "| Option | Recovery cost | Revenue at risk | Uncovered units | Rejection reasons |",
            "|---|---|---|---|---|",
        ]
        rows += [
            f"| {o.name} | {_eur(o.recovery_cost_eur)} | {_eur(o.revenue_at_risk_eur)} | "
            f"{o.uncovered_units:,} | {'; '.join(o.rejection_reasons) or '-'} |"
            for o in plan.options
        ]
        recommended = next(
            (o for o in plan.options if o.option_id == plan.recommended_option_id), None
        )
        out += [
            "## Options compared",
            *rows,
            "",
            "## Recommendation",
            f"{recommended.name if recommended else 'No feasible option'}: "
            f"{plan.recommendation_reason}",
            "",
        ]

    approval = []
    if request:
        approval.append(
            f"Request {request.approval_request_id} (version {request.payload_version}, "
            f"hash {request.payload_hash}): {_eur(request.total_cost_eur)}, needs "
            f"{', '.join(request.required_approver_roles)}"
        )
    if decision:
        verdict = "approved" if decision.approved else "rejected"
        who = ", ".join(
            f"{u} ({r})"
            for u, r in zip(decision.approver_user_ids, decision.approver_roles, strict=False)
        )
        approval.append(
            f"{verdict} at {decision.decided_at:%Y-%m-%d %H:%M} UTC"
            f"{f' by {who}' if who else ''}"
            f"{f': {decision.reason}' if decision.reason else ''}"
        )
    out += ["## Approval record", *_bullets(approval), ""]

    out += [
        "## Execution outcome",
        *_bullets(
            [
                f"{r.action_type.value} {r.action_id}: {r.status.value}"
                f"{f' ({r.external_reference})' if r.external_reference else ''} - {r.message}"
                for r in results
            ]
        ),
        "",
    ]

    risks = list(plan.residual_risks) if plan else []
    if intelligence:
        risks += [w.message for w in intelligence.warnings if w.severity != Severity.BLOCKING]
    risks += state.get("errors") or []
    out += ["## Residual risks", *_bullets(risks), ""]

    lessons = []
    if evidence:
        lessons += [f"Sources disagreed on {c.subject}." for c in evidence.conflicts]
    if plan:
        lessons += [
            f"{o.name} was rejected: {'; '.join(o.rejection_reasons)}"
            for o in plan.options
            if o.rejection_reasons
        ]
    out += ["## Lessons learned", *_bullets(lessons)]
    return "\n".join(out) + "\n"


def resolution_memory(state: dict) -> ResolutionMemory | None:
    """The approved summary for future cases; None unless the plan was executed."""
    incident, _, evidence, plan, request, _, results = _load(state)
    if not _executed(results):
        return None
    option = next(o for o in plan.options if o.option_id == request.option_id)
    lesson = (
        f" Sources disagreed on {evidence.conflicts[0].subject}."
        if (evidence and evidence.conflicts)
        else ""
    )
    return ResolutionMemory(
        memory_id=f"MEM-{incident.case_id}",  # one per case, so saving again replaces it
        case_id=incident.case_id,
        component_id=incident.component_id,
        supplier_id=incident.supplier_id,
        strategy=option.name,
        approved_summary=(
            f"{incident.disruption_type.value.replace('_', ' ')} ({incident.delay_days} d) on "
            f"{incident.component_id} resolved with '{option.name}' at "
            f"{_eur(request.total_cost_eur)}.{lesson}"
        ),
        sources=option.sources,
    )
