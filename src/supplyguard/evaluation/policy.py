"""Evaluator for the policy_and_authorization cases that recovery planning and approval cover.

Each case is checked with the deterministic components, not with an LLM answer:
supplier screening, recovery options, revalidation of edits and exact-payload approval.
It starts from the golden MC-17 incident and tests the rules, not the intake extraction.
"""

from collections.abc import Callable
from dataclasses import replace
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
from supplyguard.planning.screening import validate_order_quantity

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


def _capability(inputs: PlanningInputs, supplier_id: str):
    return next(
        (c for c in inputs.impact.supplier_capabilities if c.supplier_id == supplier_id), None
    )


def _helios_rejected(inputs: PlanningInputs, plan: RecoveryPlan, policy) -> list[str]:
    """Helios is rejected for its 21-day lead time and cannot cover the shortfall alone."""
    failures = _rejected_for("S002", ("lead time 21",))(inputs, plan, policy)
    helios = _capability(inputs, "S002")
    shortfall = inputs.impact.projected_shortfall_units
    if helios is None:
        return [*failures, "Helios has no supplier record"]
    if not any("capacity" in r for r in validate_order_quantity(helios, shortfall)):
        failures.append(
            f"buying the whole shortfall of {shortfall} from Helios is not limited by its "
            f"monthly capacity of {helios.monthly_capacity_units}"
        )
    return failures


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
    """9,000 Cordillera units break both the capacity and the EUR 75,000 cap, checked separately.

    The cap is checked with the capacity raised, so a capacity rejection cannot hide a
    missing budget check.
    """
    quantity = 9000
    request = _recommended_request(inputs, plan, policy)
    failures = []
    try:
        _revalidate(request, (_purchase("S004", quantity, "0"),), inputs, policy)
        failures.append(f"an order of {quantity} Cordillera units was accepted")
    except RevalidationError as error:
        if "capacity" not in str(error):
            failures.append(f"rejected, but not for capacity: {error}")

    cordillera = _capability(inputs, "S004")
    if cordillera is None:
        return [*failures, "Cordillera has no supplier record"]
    unlimited = cordillera.model_copy(update={"monthly_capacity_units": quantity})
    capabilities = tuple(
        unlimited if c.supplier_id == "S004" else c for c in inputs.impact.supplier_capabilities
    )
    no_capacity_limit = replace(
        inputs, impact=inputs.impact.model_copy(update={"supplier_capabilities": capabilities})
    )
    try:
        _revalidate(request, (_purchase("S004", quantity, "0"),), no_capacity_limit, policy)
        failures.append(
            f"an order of {quantity} Cordillera units within the "
            f"{inputs.incident.spending_cap_eur} EUR cap was expected to be rejected"
        )
    except RevalidationError as error:
        if "spending cap" not in str(error):
            failures.append(f"rejected, but not for the spending cap: {error}")
    return failures


def _recommended_request(inputs: PlanningInputs, plan: RecoveryPlan, policy):
    option = next(o for o in plan.options if o.option_id == plan.recommended_option_id)
    return build_approval_request(
        case_id=inputs.incident.case_id, option=option, policy=policy, requested_at=NOW
    )


def _revalidate(request, actions, inputs: PlanningInputs, policy):
    return revalidate_request(request, actions, inputs=inputs, policy=policy, requested_at=NOW)


def _with_purchase_quantity(actions, quantity: int):
    return tuple(
        a.model_copy(update={"payload": {**a.payload, "qty": quantity}})
        if a.action_type == RecoveryActionType.PURCHASE_ALTERNATIVE
        else a
        for a in actions
    )


def _edit_needs_new_approval(inputs: PlanningInputs, plan, policy) -> list[str]:
    """An invalid edit is rejected; a valid edit creates a new version the old approval misses."""
    request = _recommended_request(inputs, plan, policy)
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

    try:
        _revalidate(request, _with_purchase_quantity(request.actions, 6000), inputs, policy)
        failures.append("the 6,000-unit edit above capacity was accepted")
    except RevalidationError:
        pass

    try:
        new_request = _revalidate(
            request, _with_purchase_quantity(request.actions, 2000), inputs, policy
        )
    except RevalidationError as error:
        return [*failures, f"a valid 2,000-unit edit was rejected: {error}"]
    if new_request.payload_version != request.payload_version + 1:
        failures.append("the valid edit did not create a new approval version")
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
    "EV-0093": _helios_rejected,
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
