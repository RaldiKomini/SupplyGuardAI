"""Build and compare recovery options from deterministic inputs.

Numbers come from the operational impact report cost service.
This module only combines them, checks the rules, and explains each verdict.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from supplyguard.contracts import (
    CalculationTrace,
    ConfirmedIncident,
    ImpactReport,
    OpenPurchaseOrder,
    PurchaseCostInput,
    RecoveryAction,
    RecoveryActionType,
    RecoveryOption,
    RecoveryPlan,
    SupplierCapability,
    TransferCostInput,
)
from supplyguard.contracts.protocols import OperationalCostService
from supplyguard.planning.screening import (
    SupplierScreening,
    screen_supplier,
    validate_order_quantity,
)

REVENUE_NOTE = (
    "Revenue at risk is the case exposure before recovery; it is reported "
    "separately from recovery cost and is not an expected loss."
)


@dataclass(frozen=True)
class PlanningInputs:
    incident: ConfirmedIncident
    impact: ImpactReport
    costs: OperationalCostService
    transfer_cost_eur_per_unit: Decimal
    # The user's answer when no open purchase order shows when the delayed delivery arrives.
    expected_arrival_date: date | None = None


def missing_planning_data(inputs: PlanningInputs) -> tuple[str, ...]:
    """What the workflow must ask the user before options can be compared (empty if nothing)."""
    if _delayed_arrival_day(inputs) is None:
        return (
            f"expected arrival date of the delayed delivery from {inputs.incident.supplier_id}: "
            "no delivery date could be verified",
        )
    return ()


def build_recovery_plan(inputs: PlanningInputs) -> RecoveryPlan:
    """Compare single strategies and their combinations, then recommend one.

    When required data is missing, the plan has no options and the reason names what to ask.
    """
    incident, impact = inputs.incident, inputs.impact
    useful_day = _delayed_arrival_day(inputs)
    if useful_day is None:
        return RecoveryPlan(
            case_id=incident.case_id,
            options=(),
            recommended_option_id=None,
            recommendation_reason="Missing data: " + "; ".join(missing_planning_data(inputs)),
        )
    disrupted_upstream = _upstream_of(incident.supplier_id, impact.supplier_capabilities)
    screenings = {
        capability.supplier_id: (
            capability,
            screen_supplier(
                capability,
                latest_useful_arrival_day=useful_day,
                disrupted_upstream_id=disrupted_upstream,
            ),
        )
        for capability in impact.supplier_capabilities
        if capability.supplier_id != incident.supplier_id
    }

    transfers = _transfer_actions(inputs)
    expedites = _expedite_actions(inputs, useful_day)
    clean = [(c, s) for c, s in screenings.values() if s.feasible and not s.warnings]
    warned = [(c, s) for c, s in screenings.values() if s.feasible and s.warnings]

    shortfall = impact.projected_shortfall_units
    covered_by_expedite = sum(int(a.payload["qty"]) for a in expedites)
    remaining = max(0, shortfall - covered_by_expedite)
    purchases = _purchase_actions(inputs, clean, remaining, prefix="ACT-BUY")
    covered_by_purchases = sum(int(a.payload["qty"]) for a in purchases)
    residual = max(0, remaining - covered_by_purchases)
    top_ups = _purchase_actions(inputs, warned, residual, prefix="ACT-TOPUP")

    options: list[RecoveryOption] = []
    if transfers:
        options.append(_option(inputs, "OPT-A", "Transfer available inventory", transfers, 0))
    if expedites:
        options.append(
            _option(inputs, "OPT-B", "Expedite an existing order", expedites, covered_by_expedite)
        )
    if purchases:
        options.append(
            _option(
                inputs,
                "OPT-C",
                "Purchase from an approved alternative",
                purchases,
                covered_by_purchases,
            )
        )
    combined = (*transfers, *expedites, *purchases)
    if len({a.action_type for a in combined}) > 1:
        options.append(
            _option(
                inputs,
                "OPT-PLAN-1",
                "Combined: transfer, expedite and purchase",
                combined,
                covered_by_expedite + covered_by_purchases,
            )
        )
    if top_ups:
        warnings = tuple(w for _, s in warned for w in s.warnings)
        options.append(
            _option(
                inputs,
                "OPT-PLAN-2",
                "Combined plan plus top-up from a warned supplier",
                (*combined, *top_ups),
                covered_by_expedite
                + covered_by_purchases
                + sum(int(a.payload["qty"]) for a in top_ups),
                warnings=warnings,
            )
        )
    options.extend(_rejected_suppliers(inputs, screenings.values()))

    recommended = _recommend(options)
    return RecoveryPlan(
        case_id=incident.case_id,
        options=tuple(options),
        recommended_option_id=recommended.option_id if recommended else None,
        recommendation_reason=_reason(recommended),
        residual_risks=_residual_risks(recommended),
    )


def _delayed_arrival_day(inputs: PlanningInputs) -> int | None:
    """Days from the scenario date until the disrupted delivery now arrives (None if unknown)."""
    incident = inputs.incident
    if inputs.expected_arrival_date is not None:
        return (inputs.expected_arrival_date - incident.scenario_date).days
    if any(w.issue_type == "delayed_order_unconfirmed" for w in inputs.impact.warnings):
        return None
    # Use the same verified window that the impact service used to calculate the shortfall.
    for trace in inputs.impact.calculations:
        if trace.calculation == "projected_shortfall" and "window_days" in trace.inputs:
            return int(trace.inputs["window_days"])

    # Older impact reports may not carry the window in their calculation trace.
    orders = [
        po
        for po in inputs.impact.open_purchase_orders
        if po.supplier_id == incident.supplier_id
        and po.component_id == incident.component_id
        and po.promised_date >= incident.scenario_date
    ]
    if not orders:
        return None
    original = min(po.promised_date for po in orders)
    return (original - incident.scenario_date).days + incident.delay_days


def _upstream_of(supplier_id: str, capabilities: tuple[SupplierCapability, ...]) -> str | None:
    for capability in capabilities:
        if capability.supplier_id == supplier_id:
            return capability.upstream_manufacturer_id
    return None


def _transfer_actions(inputs: PlanningInputs) -> tuple[RecoveryAction, ...]:
    """Move hub stock to the two plant warehouses that run out first."""
    coverage = inputs.impact.coverage
    hubs = [c for c in coverage if c.factory_id is None and c.usable_units > 0]
    plants = sorted(
        (c for c in coverage if c.factory_id is not None and c.days_of_cover is not None),
        key=lambda c: c.days_of_cover,
    )[:2]
    if not hubs or not plants:
        return ()
    hub = hubs[0]
    shares = [hub.usable_units // len(plants)] * len(plants)
    shares[0] += hub.usable_units - sum(shares)
    actions = []
    for number, (plant, quantity) in enumerate(zip(plants, shares, strict=True), start=1):
        trace = inputs.costs.estimate_transfer(
            TransferCostInput(
                quantity=quantity,
                transfer_cost_eur_per_unit=inputs.transfer_cost_eur_per_unit,
                sources=hub.sources,
            )
        )
        actions.append(
            _action(
                f"ACT-TRANSFER-{number}",
                RecoveryActionType.TRANSFER_INVENTORY,
                {
                    "component_id": inputs.incident.component_id,
                    "from_location": hub.location_id,
                    "to_location": plant.location_id,
                    "qty": quantity,
                },
                trace,
            )
        )
    return tuple(actions)


def _expedite_actions(inputs: PlanningInputs, useful_day: int) -> tuple[RecoveryAction, ...]:
    actions = []
    for po in inputs.impact.open_purchase_orders:
        if po.supplier_id == inputs.incident.supplier_id or not po.expedite_allowed:
            continue
        new_day = (po.promised_date - inputs.incident.scenario_date).days - po.expedite_days_gain
        if new_day >= useful_day:
            continue
        trace = inputs.costs.estimate_expedite(po)
        actions.append(_expedite(po, trace, new_day))
    return tuple(actions)


def _expedite(po: OpenPurchaseOrder, trace: CalculationTrace, new_day: int) -> RecoveryAction:
    return _action(
        f"ACT-EXPEDITE-{po.po_id}",
        RecoveryActionType.EXPEDITE_ORDER,
        {
            "po_id": po.po_id,
            "qty": po.quantity,
            "days_gain": po.expedite_days_gain,
            "fee_pct": str(po.expedite_fee_percentage),
            "arrival_day": new_day,
        },
        trace,
    )


def _purchase_actions(
    inputs: PlanningInputs,
    candidates: list[tuple[SupplierCapability, SupplierScreening]],
    needed: int,
    *,
    prefix: str,
) -> tuple[RecoveryAction, ...]:
    """Buy from the fastest candidates, each limited to its monthly capacity."""
    actions = []
    for capability, screening in sorted(candidates, key=lambda pair: pair[1].arrival_day):
        if needed <= 0:
            break
        quantity = max(min(needed, screening.max_units), capability.minimum_order_units)
        if validate_order_quantity(capability, quantity):
            continue
        trace = inputs.costs.estimate_purchase(
            PurchaseCostInput(
                quantity=quantity,
                unit_price_eur=capability.unit_price_eur,
                sources=capability.sources,
            )
        )
        actions.append(
            _action(
                f"{prefix}-{capability.supplier_id}",
                RecoveryActionType.PURCHASE_ALTERNATIVE,
                {
                    "supplier_id": capability.supplier_id,
                    "component_id": inputs.incident.component_id,
                    "qty": quantity,
                    "unit_price_eur": str(capability.unit_price_eur),
                    "arrival_day": screening.arrival_day,
                },
                trace,
            )
        )
        needed -= quantity
    return tuple(actions)


def _rejected_suppliers(inputs, screenings) -> list[RecoveryOption]:
    return [
        RecoveryOption(
            option_id=f"OPT-REJECTED-{capability.supplier_id}",
            name=f"Purchase from {capability.supplier_id}",
            actions=(),
            recovery_cost_eur=Decimal("0"),
            revenue_at_risk_eur=inputs.impact.revenue_at_risk_eur,
            uncovered_units=inputs.impact.projected_shortfall_units,
            rejection_reasons=screening.rejection_reasons,
            sources=capability.sources,
        )
        for capability, screening in screenings
        if not screening.feasible
    ]


def _option(
    inputs: PlanningInputs,
    option_id: str,
    name: str,
    actions: tuple[RecoveryAction, ...],
    covered_units: int,
    *,
    warnings: tuple[str, ...] = (),
) -> RecoveryOption:
    cost = sum((a.cost_eur for a in actions), Decimal("0"))
    reasons = []
    if cost > inputs.incident.spending_cap_eur:
        reasons.append(f"cost {cost} EUR exceeds the {inputs.incident.spending_cap_eur} EUR cap")
    return RecoveryOption(
        option_id=option_id,
        name=name,
        actions=actions,
        recovery_cost_eur=cost,
        revenue_at_risk_eur=inputs.impact.revenue_at_risk_eur,
        uncovered_units=max(0, inputs.impact.projected_shortfall_units - covered_units),
        assumptions=(REVENUE_NOTE,),
        rejection_reasons=tuple(reasons),
        policy_warnings=warnings,
        sources=tuple(s for a in actions for s in a.sources),
    )


def _action(action_id, action_type, payload, trace: CalculationTrace) -> RecoveryAction:
    return RecoveryAction(
        action_id=action_id,
        action_type=action_type,
        payload=payload,
        cost_eur=Decimal(trace.result),
        sources=trace.sources,
    )


def _recommend(options: list[RecoveryOption]) -> RecoveryOption | None:
    """Prefer feasible options without warnings; then fewest uncovered units; then cost."""
    feasible = [o for o in options if o.actions and not o.rejection_reasons]
    if not feasible:
        return None
    return min(
        feasible,
        key=lambda o: (bool(o.policy_warnings), o.uncovered_units, o.recovery_cost_eur),
    )


def _reason(option: RecoveryOption | None) -> str:
    if option is None:
        return "No feasible recovery option within policy."
    return (
        f"{option.name}: {option.recovery_cost_eur} EUR, "
        f"{option.uncovered_units} units still uncovered, no policy warnings."
    )


def _residual_risks(option: RecoveryOption | None) -> tuple[str, ...]:
    if option is None or option.uncovered_units == 0:
        return ()
    return (
        f"{option.uncovered_units} units remain uncovered: reschedule production (E) "
        "or reallocate stock to tier-1 orders (D).",
    )
