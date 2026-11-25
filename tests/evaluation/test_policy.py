"""The policy_and_authorization cases run against planning, revalidation and approval."""

from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from planning.golden import IMPACT

from supplyguard.approval.policy import load_approval_policy
from supplyguard.contracts import InventoryPosition
from supplyguard.contracts.events import EvaluationStatus
from supplyguard.evaluation.cases import EvaluationCategory, load_cases
from supplyguard.evaluation.policy import CHECKS, PolicyEvaluator

ROOT = Path(__file__).resolve().parents[2] / "support-data"
SUITE = load_cases(ROOT / "evaluation" / "eval_cases.jsonl").cases
POLICY = load_approval_policy(ROOT / "config" / "policies.json")
POLICY_CASES = [c for c in SUITE if c.category is EvaluationCategory.POLICY_AND_AUTHORIZATION]

pytestmark = pytest.mark.asyncio


class GoldenImpactService:
    async def investigate(self, incident):
        return SimpleNamespace(impact=IMPACT)


@pytest.mark.parametrize("case", [c for c in POLICY_CASES if c.id in CHECKS], ids=lambda c: c.id)
async def test_covered_policy_case_passes_on_the_golden_scenario(case) -> None:
    result = await PolicyEvaluator(GoldenImpactService(), POLICY).evaluate(case)

    assert result.status is EvaluationStatus.PASSED, result.findings


async def test_uncovered_cases_are_reported_not_skipped() -> None:
    uncovered = [c for c in POLICY_CASES if c.id not in CHECKS]
    evaluator = PolicyEvaluator(GoldenImpactService(), POLICY)

    results = [await evaluator.evaluate(case) for case in uncovered]

    assert len(results) == 4
    assert all(r.status is EvaluationStatus.ERROR and r.findings for r in results)


async def test_a_plan_that_recommends_borealis_fails() -> None:
    expired = tuple(
        c.model_copy(update={"certification_status": "valid", "lead_time_days": 1})
        if c.supplier_id == "S003"
        else c
        for c in IMPACT.supplier_capabilities
    )

    class LaxImpactService:
        async def investigate(self, incident):
            return SimpleNamespace(
                impact=IMPACT.model_copy(update={"supplier_capabilities": expired})
            )

    case = next(c for c in POLICY_CASES if c.id == "EV-0092")
    result = await PolicyEvaluator(LaxImpactService(), POLICY).evaluate(case)

    assert result.status is EvaluationStatus.FAILED


def _with_supplier(supplier_id: str, **changes) -> GoldenImpactService:
    capabilities = tuple(
        c.model_copy(update=changes) if c.supplier_id == supplier_id else c
        for c in IMPACT.supplier_capabilities
    )
    impact = IMPACT.model_copy(update={"supplier_capabilities": capabilities})

    class ChangedImpactService:
        async def investigate(self, incident):
            return SimpleNamespace(impact=impact)

    return ChangedImpactService()


def _case(case_id: str):
    return next(c for c in POLICY_CASES if c.id == case_id)


async def test_helios_with_enough_capacity_fails_the_case() -> None:
    service = _with_supplier("S002", monthly_capacity_units=10000)

    result = await PolicyEvaluator(service, POLICY).evaluate(_case("EV-0093"))

    assert result.status is EvaluationStatus.FAILED
    assert any("capacity" in finding for finding in result.findings)


async def test_cordillera_order_within_the_cap_fails_the_case() -> None:
    # 9,000 x 5.00 = 45,000 EUR: capacity still rejects it, but the cap check must not pass.
    service = _with_supplier("S004", unit_price_eur=Decimal("5.00"))

    result = await PolicyEvaluator(service, POLICY).evaluate(_case("EV-0095"))

    assert result.status is EvaluationStatus.FAILED
    assert any("cap" in finding for finding in result.findings)


async def test_reclassifying_katowice_quarantine_as_usable_fails_the_case() -> None:
    class MisclassifiedInventory:
        async def investigate(self, incident):
            inventory = tuple(
                InventoryPosition.model_validate(
                    {**position.model_dump(), "quarantined": 0, "usable": position.usable + 600}
                )
                if position.location_id == "W-KAT"
                else position
                for position in IMPACT.inventory
            )
            impact = IMPACT.model_copy(
                update={
                    "inventory": inventory,
                    "total_usable_units": sum(position.usable for position in inventory),
                }
            )
            return SimpleNamespace(impact=impact)

    result = await PolicyEvaluator(MisclassifiedInventory(), POLICY).evaluate(_case("EV-0094"))

    assert result.status is EvaluationStatus.FAILED
    assert any("Katowice's 600 quarantined units" in finding for finding in result.findings)
