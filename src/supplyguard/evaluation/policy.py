"""Evaluator for the policy_and_authorization cases that recovery planning and approval cover.

Each case is checked with the deterministic components, not with an LLM answer:
supplier screening, recovery options, revalidation of edits and exact-payload approval.
It starts from the golden MC-17 incident and tests the rules, not the intake extraction.
"""

from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal

from supplyguard.approval.policy import ApprovalPolicy
from supplyguard.approval.requests import (
    build_approval_request,
    decision_problems,
    revalidate_request,
)
from supplyguard.approval.revalidation import RevalidationError
from supplyguard.calculations.service import DeterministicCostService
from supplyguard.contracts import (
    ApprovalDecision,
    RecoveryAction,
    RecoveryActionType,
    RecoveryPlan,
)
from supplyguard.contracts.events import EvaluationResult, EvaluationStatus
from supplyguard.contracts.incident import ConfirmedIncident
from supplyguard.contracts.protocols import OperationalIntelligenceService
from supplyguard.evaluation.cases import EvaluationCase
from supplyguard.planning.options import PlanningInputs, build_recovery_plan

GOLDEN_INCIDENT = {"supplier_id": "S001", "component_id": "MC-17", "delay_days": 12}
SCENARIO_DATE = date(2026, 10, 19)
NOW = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)

Check = Callable[[PlanningInputs, RecoveryPlan, ApprovalPolicy], list[str]]


class PolicyEvaluator:
    """Checks the planning and approval rules named in the policy_and_authorization cases."""

    def __init__(
        self,
        impact_service: OperationalIntelligenceService,
        policy: ApprovalPolicy,
        *,
        transfer_cost_eur_per_unit: Decimal = Decimal("0.12"),
        spending_cap_eur: Decimal = Decimal("75000"),
    ) -> None:
        self.impact_service = impact_service
        self.policy = policy
        self.transfer_cost_eur_per_unit = transfer_cost_eur_per_unit
        self.spending_cap_eur = spending_cap_eur

    async def evaluate(self, case: EvaluationCase) -> EvaluationResult:
        check = CHECKS.get(case.id)
        if check is None:
            return _result(case, EvaluationStatus.ERROR, (NOT_COVERED.get(case.id, OTHER),))
        incident = ConfirmedIncident(
            case_id=case.id,
            user_id="evaluation-harness",
            scenario_date=SCENARIO_DATE,
            spending_cap_eur=self.spending_cap_eur,
            original_message=case.message,
            **GOLDEN_INCIDENT,
        )
        impact = (await self.impact_service.investigate(incident)).impact
        inputs = PlanningInputs(
            incident=incident,
            impact=impact,
            costs=DeterministicCostService(),
            transfer_cost_eur_per_unit=self.transfer_cost_eur_per_unit,
        )
        failures = tuple(check(inputs, build_recovery_plan(inputs), self.policy))
        status = EvaluationStatus.PASSED if not failures else EvaluationStatus.FAILED
        return _result(case, status, failures)


def _result(case: EvaluationCase, status: EvaluationStatus, findings) -> EvaluationResult:
    return EvaluationResult(
        evaluation_case_id=case.id,
        category=case.category.value,
        status=status,
        metrics={"failed_checks": float(len(findings))}
        if status is not EvaluationStatus.ERROR
        else {},
        findings=tuple(findings),
    )


def _recommended_suppliers(plan: RecoveryPlan) -> set[str]:
    option = next((o for o in plan.options if o.option_id == plan.recommended_option_id), None)
    if option is None:
        return set()
    return {
        str(a.payload.get("supplier_id"))
        for a in option.actions
        if a.action_type == RecoveryActionType.PURCHASE_ALTERNATIVE
    }


def _rejected_for(supplier_id: str, words: tuple[str, ...]) -> Check:
    """The supplier is rejected with a reason naming the rule, and is not recommended."""

    def check(inputs: PlanningInputs, plan: RecoveryPlan, policy: ApprovalPolicy) -> list[str]:
        option = next(
            (o for o in plan.options if o.option_id == f"OPT-REJECTED-{supplier_id}"), None
        )
        failures = []
        if option is None:
            failures.append(f"{supplier_id} was not rejected")
        else:
            reasons = " ".join(option.rejection_reasons).lower()
            missing = [word for word in words if word not in reasons]
            if missing:
                failures.append(f"{supplier_id} rejection does not mention {missing}")
        if supplier_id in _recommended_suppliers(plan):
            failures.append(f"{supplier_id} is part of the recommended plan")
        return failures

    return check


def _quarantine_not_used(inputs: PlanningInputs, plan, policy) -> list[str]:
    impact = inputs.impact
    usable = sum(p.on_hand - p.reserved - p.quarantined for p in impact.inventory)
    failures = []
    if impact.total_usable_units != usable:
        failures.append(
            f"usable stock {impact.total_usable_units} includes quarantined or reserved units"
        )
    hub_units = {c.location_id: c.usable_units for c in impact.coverage}
    for option in plan.options:
        for action in option.actions:
            if action.action_type != RecoveryActionType.TRANSFER_INVENTORY:
                continue
            source = action.payload["from_location"]
            if int(action.payload["qty"]) > hub_units.get(source, 0):
                failures.append(f"{action.action_id} moves more than the usable stock at {source}")
    return failures


def _purchase(supplier_id: str, quantity: int, price: str) -> RecoveryAction:
    return RecoveryAction(
        action_id=f"ACT-EDIT-{supplier_id}",
        action_type=RecoveryActionType.PURCHASE_ALTERNATIVE,
        payload={"supplier_id": supplier_id, "component_id": "MC-17", "qty": quantity},
        cost_eur=Decimal(price),
    )


def _large_order_rejected(inputs: PlanningInputs, plan, policy) -> list[str]:
    """Ordering 9,000 from Cordillera is rejected for capacity (and the spending cap)."""
    option = next(o for o in plan.options if o.option_id == plan.recommended_option_id)
    request = build_approval_request(
        case_id=inputs.incident.case_id, option=option, policy=policy, requested_at=NOW
    )
    try:
        revalidate_request(
            request,
            (_purchase("S004", 9000, "0"),),
            inputs=inputs,
            policy=policy,
            requested_at=NOW,
        )
    except RevalidationError as error:
        if "capacity" in str(error):
            return []
        return [f"rejected, but not for capacity: {error}"]
    return ["an order of 9,000 Cordillera units was accepted"]


def _edit_needs_new_approval(inputs: PlanningInputs, plan, policy) -> list[str]:
    """After approval, changing the quantity invalidates the decision."""
    option = next(o for o in plan.options if o.option_id == plan.recommended_option_id)
    request = build_approval_request(
        case_id=inputs.incident.case_id, option=option, policy=policy, requested_at=NOW
    )
    approved = ApprovalDecision(
        approval_request_id=request.approval_request_id,
        case_id=request.case_id,
        payload_hash=request.payload_hash,
        approved=True,
        approver_user_ids=tuple(f"u-{i}" for i, _ in enumerate(request.required_approver_roles)),
        approver_roles=request.required_approver_roles,
        decided_at=NOW,
    )
    failures = []
    if decision_problems(request, approved):
        failures.append("the original approval does not authorise the original request")
    edited = tuple(
        a.model_copy(update={"payload": {**a.payload, "qty": 6000}})
        if a.action_type == RecoveryActionType.PURCHASE_ALTERNATIVE
        else a
        for a in request.actions
    )
    try:
        new_request = revalidate_request(
            request, edited, inputs=inputs, policy=policy, requested_at=NOW
        )
    except RevalidationError:
        return failures  # the edit is rejected outright: nothing can execute
    if new_request.payload_version != request.payload_version + 1:
        failures.append("the edit did not create a new approval version")
    if not decision_problems(new_request, approved):
        failures.append("the old approval still authorises the edited payload")
    return failures


def _shared_upstream_flagged(inputs: PlanningInputs, plan, policy) -> list[str]:
    warned = [w for o in plan.options for w in o.policy_warnings if "U01" in w and "upstream" in w]
    failures = []
    if not warned:
        failures.append("Delta's shared upstream with Atlas is not flagged")
    if "S005" in _recommended_suppliers(plan):
        failures.append("Delta is part of the recommended plan despite the shared upstream")
    return failures


CHECKS: dict[str, Check] = {
    "EV-0092": _rejected_for("S003", ("certification", "expired")),
    "EV-0093": _rejected_for("S002", ("lead time 21",)),
    "EV-0094": _quarantine_not_used,
    "EV-0095": _large_order_rejected,
    "EV-0096": _edit_needs_new_approval,
    "EV-0098": _shared_upstream_flagged,
}

OTHER = "not covered by the planning and approval evaluator"
NOT_COVERED = {
    "EV-0097": "needs the authorization scopes of the application layer",
    "EV-0099": "needs evidence retrieval with contract versions",
    "EV-0100": "needs evidence retrieval with source precedence",
    "EV-0101": "needs the late-penalty calculation in the impact report",
}
