"""The incident report and the approved resolution summary.

Built from templates over the saved case, not by the model, so every number and source in the
report is one the workflow received from the owning component.
"""

import json
from collections.abc import Sequence
from decimal import Decimal

from supplyguard.calculations import uncovered_after_recovery
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
        result.status is ExecutionStatus.SUCCEEDED
        or (result.status is ExecutionStatus.ALREADY_EXECUTED and result.executed_at is not None)
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


def operational_review(
    intelligence: OperationalIntelligenceResult,
    sensitivities: Sequence[dict] = (),
    *,
    order_limit: int | None = None,
) -> list[str]:
    impact = intelligence.impact
    coverage = []
    for position in sorted(
        impact.coverage, key=lambda item: (item.factory_id is None, item.location_id)
    ):
        if position.projected_stockout_date is None:
            continue
        label = (
            f"Factory stockout at {position.location_id} ({position.factory_id})"
            if position.factory_id
            else f"{position.location_id} stockout if supplying the full network alone"
        )
        coverage.append(
            f"{label}: {position.projected_stockout_date} ({_sources(position.sources)})"
        )
    shortfall_sources = tuple(
        source
        for trace in impact.calculations
        if trace.calculation == "projected_shortfall"
        for source in trace.sources
    )
    order_sources = tuple(source for order in impact.at_risk_orders for source in order.sources)
    figures = [
        f"Usable stock: {impact.total_usable_units:,} units. "
        f"Daily demand {_quantity(impact.daily_demand_units)} units.",
        f"Projected shortfall: {impact.projected_shortfall_units:,} units "
        f"({_sources(shortfall_sources or impact.sources)})",
        f"Network stockout (pooled inventory): {impact.first_stockout_date or 'not projected'}",
        *coverage,
        f"Affected products: {', '.join(impact.affected_product_ids)}. "
        f"Factories: {', '.join(impact.affected_factory_ids)}.",
        f"Open customer orders at risk: {len(impact.at_risk_orders)} ({_sources(order_sources)})",
        f"Baseline revenue exposure: {_eur(impact.revenue_at_risk_eur)} "
        f"({_sources(order_sources)})",
    ]
    if penalties := late_penalty_summary(impact):
        figures.append(penalties)
    lines = ["## Operational impact", *_bullets(figures), ""]
    if sensitivities:
        scenarios = []
        for case in sensitivities:
            shortfall = case["projected_shortfall_units"]
            value = f"{shortfall:,} units short" if shortfall is not None else "unavailable"
            scenarios.append(f"{case['delay_days']}-day delay: {value}.")
        lines += ["## Delay sensitivity", *_bullets(scenarios), ""]
    orders = sorted(
        impact.at_risk_orders, key=lambda row: (row.priority_tier, row.due_date, row.order_id)
    )
    if orders:
        lines += [
            "## Customer commitments at risk",
            "| Order | Customer | Product | Due date | Tier | Source |",
            "|---|---|---|---|---:|---|",
            *[
                f"| {row.order_id} | {row.customer_id} | {row.product_id} | "
                f"{row.due_date} | {row.priority_tier} | {_sources(row.sources)} |"
                for row in orders[:order_limit]
            ],
            "",
        ]
        if order_limit is not None and len(orders) > order_limit:
            lines += [
                f"Showing {order_limit} of {len(orders)} orders. The report lists all orders.",
                "",
            ]
    lines += ["## External risk", *_bullets(_external_risk_lines(intelligence)), ""]
    return lines


def evidence_review(evidence: EvidenceBundle) -> list[str]:
    return [
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


def _external_risk_lines(intelligence: OperationalIntelligenceResult) -> list[str]:
    lines = []
    if any(w.issue_type == "external_risk_unavailable" for w in intelligence.warnings):
        lines.append(
            "External risk checks were incomplete. Alert status is unknown for failed lookups."
        )
    elif not intelligence.external_risks:
        return ["No relevant alerts returned by the external risk feed."]
    for alert in intelligence.external_risks:
        details = (
            f"{alert.severity}, {alert.region or 'unspecified region'}, {alert.topic}. "
            f"{alert.summary}"
        )
        # Display provider fields as literal text, even if they contain Markdown or HTML.
        details = json.dumps(details, ensure_ascii=False)[1:-1].replace("`", "\\u0060")
        lines.append(f"`{details}` ({_sources(alert.sources)})")
    return lines


def options_review(plan: RecoveryPlan, *, revised: bool = False) -> list[str]:
    rows = [
        "| Option | Recovery cost | Estimated revenue exposure | Uncovered units | "
        "Warnings or rejection reasons |",
        "|---|---|---|---|---|",
    ]
    rows += [
        f"| {o.name} | {_eur(o.recovery_cost_eur)}"
        f"{' (comparison only)' if o.rejection_reasons and not o.actions else ''} | "
        f"{_eur(o.revenue_at_risk_eur)} | "
        f"{o.uncovered_units:,} | {'; '.join((*o.policy_warnings, *o.rejection_reasons)) or '-'} |"
        for o in plan.options
    ]
    recommended = next((o for o in plan.options if o.option_id == plan.recommended_option_id), None)
    name = recommended.name if recommended else "No feasible option"
    reason = plan.recommendation_reason.removeprefix(f"{name}: ")
    lines = [
        "## Original options compared" if revised else "## Options compared",
        *rows,
        "",
        "Revenue estimates prorate the case exposure by uncovered units. "
        "Customer due dates and order allocation are outside this estimate.",
        "",
    ]
    if not revised:
        lines += ["## Recommendation", f"{name}: {reason}", ""]
    return lines


def build_incident_report(state: dict) -> str:
    incident, intelligence, evidence, plan, request, decision, results = _load(state)
    revised = request is not None and request.payload_version > 1
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
    elif decision and decision.approved:
        outcome = "Execution could not be confirmed. See execution outcome and residual risks."
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
        out += operational_review(intelligence, state.get("delay_sensitivities") or ())
    if evidence:
        out += evidence_review(evidence)

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

    similar = [ResolutionMemory.model_validate(m) for m in state.get("similar_cases") or []]
    out += [
        "## Similar past incidents",
        *_bullets(
            [f"{m.case_id} ({m.memory_id}): {m.strategy}. {m.approved_summary}" for m in similar]
        ),
        "",
    ]

    if intelligence and intelligence.dependencies.findings:
        dependencies = [
            f"{finding.summary} ({_sources(finding.sources)})"
            for finding in intelligence.dependencies.findings
        ]
        out += ["## Dependency findings", *_bullets(dependencies), ""]

    if plan:
        out += options_review(plan, revised=revised)

    if request is not None and revised:
        out += [
            "## Revised recommendation",
            f"Approval v{request.payload_version} totals {_eur(request.total_cost_eur)}.",
        ]
        if intelligence is None:
            out.append("Revised coverage estimate unavailable because baseline impact is missing.")
        else:
            uncovered = uncovered_after_recovery(
                intelligence.impact.projected_shortfall_units, request.actions
            )
            out.append(f"An estimated {uncovered:,} units remain uncovered if all actions succeed.")
        out.append("")

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

    risks = list(plan.residual_risks) if plan and not revised else []
    if intelligence:
        risks += [
            w.message
            for w in intelligence.warnings
            if w.severity != Severity.BLOCKING and w.issue_type != "external_risk_unavailable"
        ]
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
