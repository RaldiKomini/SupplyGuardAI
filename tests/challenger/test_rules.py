"""The challenger accepts the reference plan and blocks weakened versions of it."""

from decimal import Decimal

from planning.golden import IMPACT, INCIDENT

from supplyguard.calculations.service import DeterministicCostService
from supplyguard.challenger.rules import challenge_plan
from supplyguard.planning.options import PlanningInputs, build_recovery_plan

CAP = Decimal("75000")
PLAN = build_recovery_plan(
    PlanningInputs(
        incident=INCIDENT,
        impact=IMPACT,
        costs=DeterministicCostService(),
        transfer_cost_eur_per_unit=Decimal("0.12"),
    )
)


def _challenge(plan):
    return challenge_plan(plan, spending_cap_eur=CAP, capabilities=IMPACT.supplier_capabilities)


def _checks(plan) -> set[str]:
    return {finding.check for finding in plan.challenge_findings}


def _replace_recommended(**changes):
    options = tuple(
        option.model_copy(update=changes) if option.option_id == "OPT-PLAN-1" else option
        for option in PLAN.options
    )
    return PLAN.model_copy(update={"options": options})


def test_reference_plan_passes() -> None:
    challenged = _challenge(PLAN)

    assert not challenged.needs_revision


def test_recommending_delta_plan_only_warns() -> None:
    challenged = _challenge(PLAN.model_copy(update={"recommended_option_id": "OPT-PLAN-2"}))

    assert not challenged.needs_revision
    assert "policy_warning" in _checks(challenged)


def test_fewer_than_three_strategies_is_blocked() -> None:
    only_c = PLAN.model_copy(
        update={
            "options": tuple(o for o in PLAN.options if o.option_id == "OPT-C"),
            "recommended_option_id": "OPT-C",
        }
    )

    challenged = _challenge(only_c)

    assert challenged.needs_revision
    assert "minimum_strategies" in _checks(challenged)


def test_double_counted_cost_is_blocked() -> None:
    challenged = _challenge(_replace_recommended(recovery_cost_eur=Decimal("60756.00")))

    assert "cost_integrity" in _checks(challenged)


def test_cost_confused_with_revenue_is_blocked() -> None:
    challenged = _challenge(
        _replace_recommended(
            recovery_cost_eur=Decimal("30378.00"), revenue_at_risk_eur=Decimal("30378.00")
        )
    )

    assert "revenue_confusion" in _checks(challenged)


def test_over_capacity_purchase_is_blocked() -> None:
    option = next(o for o in PLAN.options if o.option_id == "OPT-PLAN-1")
    actions = tuple(
        a.model_copy(update={"payload": {**a.payload, "qty": 9000}})
        if a.payload.get("supplier_id") == "S004"
        else a
        for a in option.actions
    )

    challenged = _challenge(_replace_recommended(actions=actions))

    assert "capacity" in _checks(challenged)


def test_missing_sources_are_blocked() -> None:
    option = next(o for o in PLAN.options if o.option_id == "OPT-PLAN-1")
    actions = tuple(a.model_copy(update={"sources": ()}) for a in option.actions)

    challenged = _challenge(_replace_recommended(actions=actions))

    assert "missing_evidence" in _checks(challenged)
