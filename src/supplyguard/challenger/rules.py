"""Deterministic checks that a recommendation must pass before it reaches approval.

BLOCKING findings send the plan back for revision, WARNING findings are shown
to the approver but do not stop the plan.
"""

from decimal import Decimal

from supplyguard.contracts import (
    ChallengeFinding,
    RecoveryActionType,
    RecoveryOption,
    RecoveryPlan,
    Severity,
    SupplierCapability,
)
from supplyguard.planning.screening import VALID_CERTIFICATION, validate_order_quantity

MINIMUM_STRATEGIES = 3


def challenge_plan(
    plan: RecoveryPlan,
    *,
    spending_cap_eur: Decimal,
    capabilities: tuple[SupplierCapability, ...],
) -> RecoveryPlan:
    """Return the same plan with the challenger's findings attached."""
    findings = _findings(plan, spending_cap_eur, {c.supplier_id: c for c in capabilities})
    return plan.model_copy(update={"challenge_findings": findings})


def _findings(
    plan: RecoveryPlan,
    cap: Decimal,
    capabilities: dict[str, SupplierCapability],
) -> tuple[ChallengeFinding, ...]:
    findings: list[ChallengeFinding] = []
    strategies = {
        action.action_type
        for option in plan.options
        if not option.rejection_reasons
        for action in option.actions
    }
    if len(strategies) < MINIMUM_STRATEGIES:
        findings.append(
            _blocking("minimum_strategies", f"only {len(strategies)} strategies were compared")
        )

    option = next((o for o in plan.options if o.option_id == plan.recommended_option_id), None)
    if option is None:
        findings.append(_blocking("no_recommendation", "no feasible option was recommended"))
        return tuple(findings)

    findings.extend(_option_findings(option, cap, capabilities))
    return tuple(findings)


def _option_findings(
    option: RecoveryOption,
    cap: Decimal,
    capabilities: dict[str, SupplierCapability],
) -> list[ChallengeFinding]:
    oid = option.option_id
    findings: list[ChallengeFinding] = []
    action_total = sum((a.cost_eur for a in option.actions), Decimal("0"))

    if option.rejection_reasons:
        findings.append(_blocking("policy_violation", "; ".join(option.rejection_reasons), oid))
    if option.recovery_cost_eur > cap:
        findings.append(
            _blocking("spending_cap", f"{option.recovery_cost_eur} EUR exceeds {cap} EUR", oid)
        )
    if option.recovery_cost_eur != action_total:
        findings.append(
            _blocking(
                "cost_integrity",
                "recovery cost differs from the sum of its actions (possible double counting)",
                oid,
            )
        )
    if option.recovery_cost_eur > 0 and option.recovery_cost_eur == option.revenue_at_risk_eur:
        findings.append(_blocking("revenue_confusion", "recovery cost equals revenue at risk", oid))
    if any(not action.sources for action in option.actions):
        findings.append(_blocking("missing_evidence", "an action has no source", oid))
    if not option.assumptions:
        findings.append(_warning("unstated_assumptions", "no assumptions are stated", oid))
    for warning in option.policy_warnings:
        findings.append(_warning("policy_warning", warning, oid))

    for action in option.actions:
        if action.action_type != RecoveryActionType.PURCHASE_ALTERNATIVE:
            continue
        supplier_id = action.payload.get("supplier_id")
        capability = capabilities.get(str(supplier_id))
        if capability is None:
            findings.append(_blocking("unknown_supplier", f"{supplier_id} has no record", oid))
            continue
        if capability.certification_status != VALID_CERTIFICATION:
            findings.append(
                _blocking("certification", f"{supplier_id} certification is not valid", oid)
            )
        for reason in validate_order_quantity(capability, int(action.payload["qty"])):
            findings.append(_blocking("capacity", f"{supplier_id}: {reason}", oid))
    return findings


def _blocking(check: str, message: str, option_id: str | None = None) -> ChallengeFinding:
    return ChallengeFinding(
        check=check, message=message, severity=Severity.BLOCKING, option_id=option_id
    )


def _warning(check: str, message: str, option_id: str | None = None) -> ChallengeFinding:
    return ChallengeFinding(
        check=check, message=message, severity=Severity.WARNING, option_id=option_id
    )
