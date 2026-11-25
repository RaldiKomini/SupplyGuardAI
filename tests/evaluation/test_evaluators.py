from pathlib import Path
from types import SimpleNamespace

import pytest

from supplyguard.contracts.events import EvaluationStatus
from supplyguard.evaluation.cases import EvaluationCase, EvaluationCategory, load_cases
from supplyguard.evaluation.evaluators import (
    GoldenFigures,
    ImpactEvaluator,
    PromptInjectionEvaluator,
    default_evaluators,
    load_golden_figures,
)
from supplyguard.evaluation.report import build_report, render_markdown
from supplyguard.evaluation.runner import EvaluationRunner
from supplyguard.retrieval.adapters import GuardrailContentSafetyService

SUPPORT = Path(__file__).resolve().parents[2] / "support-data" / "evaluation"
SUITE = load_cases(SUPPORT / "eval_cases.jsonl").cases

pytestmark = pytest.mark.asyncio


def by_id(case_id: str) -> EvaluationCase:
    return next(item for item in SUITE if item.id == case_id)


class FakeImpactService:
    def __init__(self, impact) -> None:
        self.impact = impact
        self.incidents = []

    async def investigate(self, incident):
        self.incidents.append(incident)
        return SimpleNamespace(impact=self.impact)


def impact_of(*, products, orders=0, usable=0, shortfall=0):
    return SimpleNamespace(
        affected_product_ids=tuple(products),
        at_risk_orders=tuple(range(orders)),
        total_usable_units=usable,
        projected_shortfall_units=shortfall,
    )


def golden_figures() -> GoldenFigures:
    return load_golden_figures(SUPPORT / "golden_scenario.json")


async def test_golden_figures_come_from_the_supplied_scenario() -> None:
    figures = golden_figures()

    assert figures.usable_units == 3200
    assert figures.shortfall_units == 5350
    assert figures.open_orders_due_within_60d == 77
    assert figures.affected_product_ids == {"P012", "P023", "P031", "P047", "P058", "P066"}


async def test_impact_case_passes_when_products_and_orders_match() -> None:
    case = by_id("EV-0002")
    service = FakeImpactService(impact_of(products=case.expected["affected_products"], orders=45))

    result = await ImpactEvaluator(service).evaluate(case)

    assert result.status is EvaluationStatus.PASSED
    assert service.incidents[0].supplier_id == case.expected["supplier_id"]
    assert service.incidents[0].delay_days == case.expected["delay_days"]


async def test_impact_case_fails_with_a_finding_per_mismatch() -> None:
    case = by_id("EV-0002")
    service = FakeImpactService(impact_of(products=["P011"], orders=12))

    result = await ImpactEvaluator(service).evaluate(case)

    assert result.status is EvaluationStatus.FAILED
    assert len(result.findings) == 2
    assert "open orders due within 60 days expected 45, got 12" in result.findings[1]


async def test_golden_case_checks_the_supplied_figures() -> None:
    case = by_id("EV-0001")
    figures = golden_figures()
    service = FakeImpactService(
        impact_of(
            products=figures.affected_product_ids,
            orders=figures.open_orders_due_within_60d,
            usable=figures.usable_units,
            shortfall=figures.shortfall_units,
        )
    )

    result = await ImpactEvaluator(service, golden=figures).evaluate(case)

    assert result.status is EvaluationStatus.PASSED
    assert result.metrics["checks"] == 4.0


async def test_golden_case_fails_when_shortfall_is_wrong() -> None:
    case = by_id("EV-0001")
    figures = golden_figures()
    service = FakeImpactService(
        impact_of(
            products=figures.affected_product_ids,
            orders=figures.open_orders_due_within_60d,
            usable=figures.usable_units,
            shortfall=5000,
        )
    )

    result = await ImpactEvaluator(service, golden=figures).evaluate(case)

    assert result.status is EvaluationStatus.FAILED
    assert result.findings == ("shortfall expected 5350, got 5000",)


async def test_case_with_no_expected_impact_values_is_not_a_vacuous_pass() -> None:
    case = EvaluationCase(
        id="X",
        category=EvaluationCategory.EXTRACTION_AND_IMPACT,
        message="m",
        expected={"supplier_id": "S001", "component_id": "MC-17", "delay_days": 3},
    )

    result = await ImpactEvaluator(FakeImpactService(impact_of(products=[]))).evaluate(case)

    assert result.status is EvaluationStatus.FAILED
    assert result.findings == ("case has no expected impact values to check",)


async def test_every_supplied_prompt_injection_case_is_flagged() -> None:
    cases = [item for item in SUITE if item.category is EvaluationCategory.PROMPT_INJECTION]
    evaluator = PromptInjectionEvaluator(GuardrailContentSafetyService())

    results = [await evaluator.evaluate(item) for item in cases]

    assert len(cases) == 10
    assert {result.status for result in results} == {EvaluationStatus.PASSED}


async def test_clean_golden_message_is_not_flagged_as_injection() -> None:
    evaluator = PromptInjectionEvaluator(GuardrailContentSafetyService())

    result = await evaluator.evaluate(by_id("EV-0001"))

    assert result.status is EvaluationStatus.FAILED
    assert result.metrics["injection_findings"] == 0.0


async def test_default_evaluators_cover_every_category() -> None:
    evaluators = default_evaluators(
        FakeImpactService(impact_of(products=[])), GuardrailContentSafetyService(), None
    )

    assert set(evaluators) == set(EvaluationCategory)


async def test_full_run_on_supplied_suite_produces_a_report() -> None:
    service = FakeImpactService(impact_of(products=[]))
    runner = EvaluationRunner(
        default_evaluators(service, GuardrailContentSafetyService(), golden_figures())
    )

    results = await runner.run(SUITE)
    report = build_report(SUITE, results)

    assert report.total_cases == 105
    assert report.recorded_results == 105
    assert report.overall_passed is False
    summary = {s.category: s for s in report.categories}
    assert summary["prompt_injection"].passed == 10
    assert summary["ambiguity"].errored == 10
    assert summary["follow_up"].errored == 4
    assert "NOT PASSED" in render_markdown(report)
