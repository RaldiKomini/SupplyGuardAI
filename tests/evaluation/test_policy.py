"""The policy_and_authorization cases run against planning, revalidation and approval."""

from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from planning.golden import IMPACT

from supplyguard.approval.policy import load_approval_policy
from supplyguard.auth import get_registry
from supplyguard.contracts import AtRiskOrder, EvidenceBundle, EvidenceConflict, InventoryPosition
from supplyguard.contracts.events import EvaluationStatus
from supplyguard.evaluation.cases import EvaluationCategory, load_cases
from supplyguard.evaluation.langsmith_experiment import approver_contexts
from supplyguard.evaluation.policy import (
    CHECKS,
    EVIDENCE_CHECKS,
    NEEDS_EVIDENCE,
    PolicyEvaluator,
)
from supplyguard.planning.options import build_recovery_plan
from supplyguard.retrieval.service import EvidenceService

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


async def test_every_policy_case_has_a_check() -> None:
    assert {c.id for c in POLICY_CASES} == set(CHECKS) | set(EVIDENCE_CHECKS)


async def test_evidence_cases_without_the_evidence_service_are_reported_not_skipped() -> None:
    evaluator = PolicyEvaluator(GoldenImpactService(), POLICY)

    results = [await evaluator.evaluate(_case(case_id)) for case_id in EVIDENCE_CHECKS]

    assert len(results) == 3
    assert all(r.status is EvaluationStatus.ERROR for r in results)
    assert all(r.findings == (NEEDS_EVIDENCE,) for r in results)


class DelayAwareImpactService:
    """The golden impact; a longer delay leaves 100 more units uncovered per extra day."""

    async def investigate(self, incident):
        extra = (incident.delay_days - 12) * 100
        shortfall = IMPACT.projected_shortfall_units + extra
        return SimpleNamespace(
            impact=IMPACT.model_copy(update={"projected_shortfall_units": shortfall})
        )


@pytest.fixture(scope="module")
def evidence() -> EvidenceService:
    return EvidenceService.from_support_data(ROOT, backend="memory")


@pytest.fixture(scope="module")
def contexts():
    return approver_contexts(get_registry())


def _evidence_evaluator(evidence, contexts, impact=None, **changes) -> PolicyEvaluator:
    return PolicyEvaluator(
        impact or DelayAwareImpactService(),
        POLICY,
        evidence=evidence,
        authorizations={**contexts, **changes},
    )


@pytest.mark.parametrize("case_id", sorted(EVIDENCE_CHECKS))
async def test_evidence_case_passes_with_the_supplied_documents(
    case_id, evidence, contexts
) -> None:
    result = await _evidence_evaluator(evidence, contexts).evaluate(_case(case_id))

    assert result.status is EvaluationStatus.PASSED, result.findings


async def test_a_manager_with_the_finance_scope_fails_the_case(evidence, contexts) -> None:
    manager = contexts["procurement_manager"]
    widened = manager.model_copy(
        update={"allowed_scopes": (*manager.allowed_scopes, "finance-confidential")}
    )

    result = await _evidence_evaluator(evidence, contexts, procurement_manager=widened).evaluate(
        _case("EV-0097")
    )

    assert result.status is EvaluationStatus.FAILED
    assert any("without the scope" in finding for finding in result.findings)


class OldContractEvidence:
    """Plans the Atlas lead time from contract v2, as if precedence were ignored."""

    async def retrieve(self, incident, authorization):
        return EvidenceBundle(
            case_id=incident.case_id,
            query="",
            items=(),
            conflicts=(
                EvidenceConflict(
                    subject="S001.lead_time_days",
                    description="planning value 10 from DOC-CON-S001-v2",
                    selected_evidence_id="DOC-CON-S001-v2#c0",
                    conflicting_evidence_ids=("DOC-CON-S001-v3#c0",),
                ),
            ),
        )

    def search_bundle(self, case_id, query, authorization):
        return EvidenceBundle(case_id=case_id, query=query, items=())


async def test_planning_from_the_superseded_contract_fails_the_case(contexts) -> None:
    result = await _evidence_evaluator(OldContractEvidence(), contexts).evaluate(_case("EV-0099"))

    assert result.status is EvaluationStatus.FAILED
    assert any("not v3" in finding for finding in result.findings)


async def test_a_sensitivity_that_changes_nothing_fails_the_case(evidence, contexts) -> None:
    result = await _evidence_evaluator(evidence, contexts, impact=GoldenImpactService()).evaluate(
        _case("EV-0100")
    )

    assert result.status is EvaluationStatus.FAILED
    assert any("16-day delay" in finding for finding in result.findings)


async def test_a_penalty_added_to_the_recovery_cost_fails_the_case() -> None:
    tier_one = AtRiskOrder(
        order_id="SO-1",
        customer_id="C1",
        product_id="P012",
        due_date=IMPACT.first_stockout_date or date(2026, 11, 1),
        quantity=10,
        priority_tier=1,
        revenue_eur=Decimal("814560.78"),  # 5% = 40,728.039, rounded to 40,728.04
    )
    impact = IMPACT.model_copy(update={"at_risk_orders": (tier_one,)})
    evaluator = PolicyEvaluator(GoldenImpactService(), POLICY)
    inputs = await evaluator.inputs_for(evaluator.incident("EV-0101", "test"))
    inputs = replace(inputs, impact=impact)
    plan = build_recovery_plan(inputs)
    padded = plan.model_copy(
        update={
            "options": tuple(
                o.model_copy(update={"recovery_cost_eur": o.recovery_cost_eur + 500})
                for o in plan.options
            )
        }
    )

    assert CHECKS["EV-0101"](inputs, plan, POLICY) == []
    failures = CHECKS["EV-0101"](inputs, padded, POLICY)
    assert any("is not the sum of its actions" in finding for finding in failures)


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
