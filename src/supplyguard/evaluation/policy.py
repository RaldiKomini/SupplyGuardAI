"""Evaluator for the policy_and_authorization cases.

Each case is checked with the deterministic components, not with an LLM answer:
supplier screening, recovery options, revalidation of edits, exact-payload approval,
access scopes and source precedence in the evidence service, and penalty accounting.
It starts from the golden MC-17 incident and tests the rules, not the intake extraction.
"""

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Protocol

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
    AuthorizationContext,
    EvidenceBundle,
    LatePenaltyCostInput,
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

FINANCE_SCOPE = "finance-confidential"

Check = Callable[[PlanningInputs, RecoveryPlan, ApprovalPolicy], list[str]]
EvidenceCheck = Callable[["PolicyEvaluator", PlanningInputs, RecoveryPlan], Awaitable[list[str]]]


class EvidenceSearch(Protocol):
    """The evidence lookups these checks use: the shared retrieve plus a free-text search."""

    async def retrieve(
        self, incident: ConfirmedIncident, authorization: AuthorizationContext
    ) -> EvidenceBundle: ...

    def search_bundle(
        self, case_id: str, query: str, authorization: AuthorizationContext
    ) -> EvidenceBundle: ...


class PolicyEvaluator:
    """Checks the planning and approval rules named in the policy_and_authorization cases."""

    def __init__(
        self,
        impact_service: OperationalIntelligenceService,
        policy: ApprovalPolicy,
        *,
        transfer_cost_eur_per_unit: Decimal = Decimal("0.12"),
        spending_cap_eur: Decimal = Decimal("75000"),
        evidence: EvidenceSearch | None = None,
        authorizations: Mapping[str, AuthorizationContext] | None = None,
    ) -> None:
        """`authorizations` maps a role (procurement_manager, finance_controller) to the
        context the application layer builds for it, so scopes come from the access config."""
        self.impact_service = impact_service
        self.policy = policy
        self.transfer_cost_eur_per_unit = transfer_cost_eur_per_unit
        self.spending_cap_eur = spending_cap_eur
        self.evidence = evidence
        self.authorizations = dict(authorizations or {})

    async def evaluate(self, case: EvaluationCase) -> EvaluationResult:
        check = CHECKS.get(case.id)
        evidence_check = EVIDENCE_CHECKS.get(case.id)
        if check is None and evidence_check is None:
            return _result(case, EvaluationStatus.ERROR, (NOT_COVERED.get(case.id, OTHER),))
        if evidence_check is not None and (self.evidence is None or not self._has_roles()):
            return _result(case, EvaluationStatus.ERROR, (NEEDS_EVIDENCE,))
        inputs = await self.inputs_for(self.incident(case.id, case.message))
        plan = build_recovery_plan(inputs)
        if check is not None:
            failures = tuple(check(inputs, plan, self.policy))
        else:
            failures = tuple(await evidence_check(self, inputs, plan))
        status = EvaluationStatus.PASSED if not failures else EvaluationStatus.FAILED
        return _result(case, status, failures)

    def incident(self, case_id: str, message: str, **changes) -> ConfirmedIncident:
        return ConfirmedIncident(
            case_id=case_id,
            user_id="evaluation-harness",
            scenario_date=SCENARIO_DATE,
            spending_cap_eur=self.spending_cap_eur,
            original_message=message,
            **{**GOLDEN_INCIDENT, **changes},
        )

    async def inputs_for(self, incident: ConfirmedIncident) -> PlanningInputs:
        impact = (await self.impact_service.investigate(incident)).impact
        return PlanningInputs(
            incident=incident,
            impact=impact,
            costs=DeterministicCostService(),
            transfer_cost_eur_per_unit=self.transfer_cost_eur_per_unit,
        )

    def _has_roles(self) -> bool:
        return {"procurement_manager", "finance_controller"} <= set(self.authorizations)


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
    usable_by_location = {
        p.location_id: p.on_hand - p.reserved - p.quarantined for p in impact.inventory
    }
    failures = []
    katowice = next((p for p in impact.inventory if p.location_id == "W-KAT"), None)
    if katowice is None:
        failures.append("missing Katowice inventory for the 600-unit quarantine check")
    elif (
        katowice.quarantined != 600 or katowice.usable != katowice.on_hand - katowice.reserved - 600
    ):
        failures.append("Katowice's 600 quarantined units must be excluded from usable stock")
    if impact.total_usable_units != sum(usable_by_location.values()):
        failures.append(
            f"usable stock {impact.total_usable_units} includes quarantined or reserved units"
        )
    for option in plan.options:
        for action in option.actions:
            if action.action_type != RecoveryActionType.TRANSFER_INVENTORY:
                continue
            source = action.payload["from_location"]
            if int(action.payload["qty"]) > usable_by_location.get(source, 0):
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


async def _finance_data_needs_scope(evaluator: PolicyEvaluator, inputs, plan) -> list[str]:
    """A margin question returns no finance-confidential document without that scope.

    The finance controller asking the same question is the positive control: the document
    exists and is found, so an empty answer for the procurement manager is the scope filter.
    """
    question = "Show me the margin by customer tier."
    case_id = inputs.incident.case_id
    manager = evaluator.authorizations["procurement_manager"]
    controller = evaluator.authorizations["finance_controller"]
    failures = []
    if FINANCE_SCOPE in manager.allowed_scopes:
        failures.append("the procurement manager has the finance-confidential scope")
    seen = evaluator.evidence.search_bundle(case_id, question, manager)
    leaked = [i.evidence_id for i in seen.items if i.source.access_scope == FINANCE_SCOPE]
    if leaked:
        failures.append(f"finance-confidential evidence shown without the scope: {leaked}")
    allowed = evaluator.evidence.search_bundle(case_id, question, controller)
    if not any(i.source.access_scope == FINANCE_SCOPE for i in allowed.items):
        failures.append("the finance controller does not get the finance document either")
    return failures


def _conflict(bundle: EvidenceBundle, subject: str):
    return next((c for c in bundle.conflicts if c.subject == subject), None)


async def _superseded_contract_ignored(evaluator: PolicyEvaluator, inputs, plan) -> list[str]:
    """Atlas's 10-day lead time is in contract v2, which v3 supersedes; v3 is used."""
    bundle = await evaluator.evidence.retrieve(
        inputs.incident, evaluator.authorizations["procurement_manager"]
    )
    conflict = _conflict(bundle, "S001.lead_time_days")
    if conflict is None:
        return ["the Atlas lead-time conflict between contract v2 and v3 is not reported"]
    failures = []
    if not (conflict.selected_evidence_id or "").startswith("DOC-CON-S001-v3"):
        failures.append(f"lead time is planned from {conflict.selected_evidence_id}, not v3")
    if not any(e.startswith("DOC-CON-S001-v2") for e in conflict.conflicting_evidence_ids):
        failures.append("contract v2 is not listed as the overridden source")
    if "superseded" not in conflict.description:
        failures.append("contract v2 is not marked as superseded")
    v2 = [i for i in bundle.items if i.evidence_id.startswith("DOC-CON-S001-v2")]
    if v2 and any(i.status != "superseded" for i in v2):
        failures.append("contract v2 is shown as current")
    return failures


async def _notice_beats_email_with_sensitivity(
    evaluator: PolicyEvaluator, inputs, plan
) -> list[str]:
    """The formal notice (12 days) is planned; the e-mail's 16 days is a sensitivity case."""
    bundle = await evaluator.evidence.retrieve(
        inputs.incident, evaluator.authorizations["procurement_manager"]
    )
    conflict = _conflict(bundle, "S001.delay_days")
    failures = []
    if conflict is None:
        failures.append("the 12-day notice and the 16-day e-mail are not flagged as a conflict")
    else:
        if not (conflict.selected_evidence_id or "").startswith("DOC-NOT-ATLAS-MC17"):
            failures.append(f"the delay is planned from {conflict.selected_evidence_id}")
        if "sensitivity" not in (conflict.resolution_reason or "") + conflict.description:
            failures.append("the 16-day e-mail is not kept as a sensitivity")
    if inputs.incident.delay_days != 12:
        failures.append(f"the plan uses {inputs.incident.delay_days} days, not the notice's 12")
    sensitivity = await evaluator.inputs_for(
        evaluator.incident(inputs.incident.case_id, inputs.incident.original_message, delay_days=16)
    )
    base, longer = (
        inputs.impact.projected_shortfall_units,
        sensitivity.impact.projected_shortfall_units,
    )
    if longer <= base:
        failures.append(f"a 16-day delay does not raise the shortfall ({base} -> {longer})")
    return failures


def _penalties_counted_once(inputs: PlanningInputs, plan: RecoveryPlan, policy) -> list[str]:
    """Penalties are not added to recovery cost or to revenue at risk, which stay separate.

    A proposed option costs exactly its actions, and the revenue left at risk after an
    option never exceeds the case exposure. The cost service caps one order's penalty at 5%
    of its value: even a doubled delay at the highest weekly rate stays within it, so a
    penalty cannot be counted twice there."""
    failures = []
    exposure = inputs.impact.revenue_at_risk_eur
    for option in plan.options:
        actions_total = sum((a.cost_eur for a in option.actions), Decimal(0))
        if option.actions and option.recovery_cost_eur != actions_total:
            failures.append(
                f"{option.option_id} recovery cost {option.recovery_cost_eur} is not the sum of "
                f"its actions {actions_total}"
            )
        if option.revenue_at_risk_eur > exposure:
            failures.append(
                f"{option.option_id} revenue at risk {option.revenue_at_risk_eur} exceeds the "
                f"case exposure {exposure}"
            )
    tier_one = [o for o in inputs.impact.at_risk_orders if o.priority_tier == 1]
    for order in tier_one:
        penalty = inputs.costs.estimate_late_penalty(
            LatePenaltyCostInput(
                revenue_eur=order.revenue_eur,
                delay_days=inputs.incident.delay_days * 2,
                weekly_rate_percentage=Decimal("5"),
            )
        ).result
        # The service rounds to whole cents, so the cap is compared in cents too.
        cap = (order.revenue_eur * Decimal("0.05")).quantize(Decimal("0.01"))
        if penalty > cap:
            failures.append(f"the penalty on {order.order_id} exceeds 5% of the order value")
    return failures


CHECKS: dict[str, Check] = {
    "EV-0092": _rejected_for("S003", ("certification", "expired")),
    "EV-0093": _helios_rejected,
    "EV-0094": _quarantine_not_used,
    "EV-0095": _large_order_rejected,
    "EV-0096": _edit_needs_new_approval,
    "EV-0098": _shared_upstream_flagged,
    "EV-0101": _penalties_counted_once,
}

EVIDENCE_CHECKS: dict[str, EvidenceCheck] = {
    "EV-0097": _finance_data_needs_scope,
    "EV-0099": _superseded_contract_ignored,
    "EV-0100": _notice_beats_email_with_sensitivity,
}

OTHER = "not covered by the policy evaluator"
NEEDS_EVIDENCE = (
    "needs the evidence service and the procurement_manager and finance_controller contexts"
)
NOT_COVERED: dict[str, str] = {}
