"""Recovery options must reproduce the MC-17 reference plans (golden_scenario.json)."""

from dataclasses import replace
from datetime import date
from decimal import Decimal

from supplyguard.calculations.service import DeterministicCostService
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

    assert plan_1.revenue_at_risk_eur == Decimal("37983380")
    assert plan_1.recovery_cost_eur != plan_1.revenue_at_risk_eur


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
