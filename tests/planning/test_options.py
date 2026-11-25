"""Recovery options must reproduce the MC-17 reference plans (golden_scenario.json)."""

from dataclasses import replace
from datetime import date
from decimal import Decimal

from supplyguard.calculations.service import DeterministicCostService
from supplyguard.contracts import CalculationTrace, DataQualityWarning, Severity, SourceReference
from supplyguard.planning.options import (
    PlanningInputs,
    build_recovery_plan,
    missing_planning_data,
)

from .golden import IMPACT, INCIDENT

INPUTS = PlanningInputs(
    incident=INCIDENT,
    impact=IMPACT,
    costs=DeterministicCostService(),
    transfer_cost_eur_per_unit=Decimal("0.12"),
)
PLAN = build_recovery_plan(INPUTS)
OPTIONS = {option.option_id: option for option in PLAN.options}


def test_at_least_three_strategies_are_compared() -> None:
    assert {"OPT-A", "OPT-B", "OPT-C"} <= OPTIONS.keys()


def test_plan_1_matches_reference_cost_and_gap() -> None:
    plan_1 = OPTIONS["OPT-PLAN-1"]

    assert plan_1.recovery_cost_eur == Decimal("30378.00")
    assert plan_1.uncovered_units == 850
    assert plan_1.policy_warnings == ()


def test_transfers_go_to_the_plants_that_run_out_first() -> None:
    destinations = {a.payload["to_location"] for a in OPTIONS["OPT-A"].actions}

    assert destinations == {"W-LYO", "W-KAT"}
    assert OPTIONS["OPT-A"].recovery_cost_eur == Decimal("96.00")


def test_cordillera_is_limited_to_its_capacity() -> None:
    (purchase,) = OPTIONS["OPT-C"].actions

    assert purchase.payload["supplier_id"] == "S004"
    assert purchase.payload["qty"] == 3000


def test_plan_2_closes_the_gap_with_delta_but_carries_a_warning() -> None:
    plan_2 = OPTIONS["OPT-PLAN-2"]

    assert plan_2.recovery_cost_eur == Decimal("39218.00")
    assert plan_2.uncovered_units == 0
    assert any("U01" in warning for warning in plan_2.policy_warnings)


def test_rejected_suppliers_explain_why() -> None:
    assert any("expired" in r for r in OPTIONS["OPT-REJECTED-S003"].rejection_reasons)
    assert any("lead time 21" in r for r in OPTIONS["OPT-REJECTED-S002"].rejection_reasons)


def test_plan_1_is_recommended_and_residual_gap_is_reported() -> None:
    assert PLAN.recommended_option_id == "OPT-PLAN-1"
    assert any("850 units" in risk for risk in PLAN.residual_risks)


def test_revenue_at_risk_is_kept_separate_from_cost() -> None:
    plan_1 = OPTIONS["OPT-PLAN-1"]

    # 850 of the 5,350 shortfall units stay uncovered: 37,983,380 x 850 / 5,350.
    assert plan_1.revenue_at_risk_eur == Decimal("6034742.62")
    assert plan_1.recovery_cost_eur != plan_1.revenue_at_risk_eur


def test_revenue_at_risk_follows_the_units_each_option_leaves_uncovered() -> None:
    exposure = IMPACT.revenue_at_risk_eur
    for option in PLAN.options:
        expected = exposure * option.uncovered_units / IMPACT.projected_shortfall_units
        assert abs(option.revenue_at_risk_eur - expected) < Decimal("0.01"), option.option_id

    full = [o for o in PLAN.options if o.uncovered_units == 0 and not o.rejection_reasons]
    assert full and all(o.revenue_at_risk_eur == 0 for o in full)
    assert OPTIONS["OPT-PLAN-1"].revenue_at_risk_eur < exposure
    assert OPTIONS["OPT-REJECTED-S002"].revenue_at_risk_eur == exposure
    assert len({o.revenue_at_risk_eur for o in PLAN.options}) > 2


def test_rejected_suppliers_show_their_estimated_cost_not_zero() -> None:
    for supplier_id in ("S002", "S003"):
        option = OPTIONS[f"OPT-REJECTED-{supplier_id}"]
        capability = next(c for c in IMPACT.supplier_capabilities if c.supplier_id == supplier_id)
        quantity = min(IMPACT.projected_shortfall_units, capability.monthly_capacity_units)

        assert option.recovery_cost_eur == quantity * capability.unit_price_eur
        assert option.recovery_cost_eur > 0
        assert not option.actions
        assert any("for comparison only" in a for a in option.assumptions)


def test_minimum_order_above_capacity_rejects_the_supplier() -> None:
    capabilities = tuple(
        c.model_copy(update={"minimum_order_units": 3500}) if c.supplier_id == "S004" else c
        for c in IMPACT.supplier_capabilities
    )
    impact = IMPACT.model_copy(update={"supplier_capabilities": capabilities})

    plan = build_recovery_plan(replace(INPUTS, impact=impact))
    options = {option.option_id: option for option in plan.options}

    assert "OPT-C" not in options
    assert any("minimum order 3500" in r for r in options["OPT-REJECTED-S004"].rejection_reasons)


def test_missing_open_order_asks_for_the_arrival_date() -> None:
    orders = tuple(po for po in IMPACT.open_purchase_orders if po.supplier_id != "S001")
    no_order = replace(INPUTS, impact=IMPACT.model_copy(update={"open_purchase_orders": orders}))

    plan = build_recovery_plan(no_order)

    assert plan.options == ()
    assert plan.recommended_option_id is None
    assert plan.recommendation_reason.startswith("Missing data")
    assert any("expected arrival date" in item for item in missing_planning_data(no_order))


def test_users_arrival_date_replaces_the_missing_order() -> None:
    orders = tuple(po for po in IMPACT.open_purchase_orders if po.supplier_id != "S001")
    answered = replace(
        INPUTS,
        impact=IMPACT.model_copy(update={"open_purchase_orders": orders}),
        expected_arrival_date=date(2026, 11, 7),
    )

    assert missing_planning_data(answered) == ()
    assert build_recovery_plan(answered).recommended_option_id == "OPT-PLAN-1"


def test_recovery_uses_the_arrival_window_for_the_named_order() -> None:
    later_order = IMPACT.open_purchase_orders[0].model_copy(
        update={"po_id": "PO-2026-9010", "promised_date": date(2026, 10, 28)}
    )
    trace = CalculationTrace(
        calculation="projected_shortfall",
        formula="ceil(max(0, demand_before_arrival - usable))",
        inputs={"window_days": 21},
        result=6250,
        unit="units",
        sources=(
            SourceReference(
                source_id="purchase_orders.csv", source_type="csv", record_id=later_order.po_id
            ),
        ),
    )
    capabilities = tuple(
        c.model_copy(update={"lead_time_days": 20}) if c.supplier_id == "S004" else c
        for c in IMPACT.supplier_capabilities
    )
    impact = IMPACT.model_copy(
        update={
            "open_purchase_orders": (*IMPACT.open_purchase_orders, later_order),
            "calculations": (trace,),
            "projected_shortfall_units": trace.result,
            "supplier_capabilities": capabilities,
        }
    )
    incident = INCIDENT.model_copy(
        update={"original_message": "Atlas reports a 12-day delay for PO-2026-9010."}
    )

    plan = build_recovery_plan(replace(INPUTS, incident=incident, impact=impact))

    # The 20-day alternative is useful for the verified 21-day window.
    purchase = next(option for option in plan.options if option.option_id == "OPT-C")
    assert purchase.actions[0].payload["supplier_id"] == "S004"
    assert purchase.actions[0].payload["arrival_day"] == 20


def test_unverified_order_requires_clarification_even_with_another_open_order() -> None:
    warning = DataQualityWarning(
        issue_type="delayed_order_unconfirmed",
        message="The named order could not be verified.",
        severity=Severity.WARNING,
    )
    inputs = replace(INPUTS, impact=IMPACT.model_copy(update={"warnings": (warning,)}))

    assert missing_planning_data(inputs)
    assert build_recovery_plan(inputs).options == ()
    assert missing_planning_data(replace(inputs, expected_arrival_date=date(2026, 11, 7))) == ()
