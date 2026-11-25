import pytest

from supplyguard.contracts.events import EvaluationResult, EvaluationStatus
from supplyguard.evaluation.cases import EvaluationCase, EvaluationCategory
from supplyguard.evaluation.runner import EvaluationRunner, UnavailableEvaluator

pytestmark = pytest.mark.asyncio


def case(case_id: str, category: EvaluationCategory) -> EvaluationCase:
    return EvaluationCase(id=case_id, category=category, message="m")


def passed(item: EvaluationCase) -> EvaluationResult:
    return EvaluationResult(
        evaluation_case_id=item.id,
        category=item.category.value,
        status=EvaluationStatus.PASSED,
    )


class PassingEvaluator:
    async def evaluate(self, item: EvaluationCase) -> EvaluationResult:
        return passed(item)


class RaisingEvaluator:
    async def evaluate(self, item: EvaluationCase) -> EvaluationResult:
        raise RuntimeError("database unavailable")


class WrongCaseEvaluator:
    async def evaluate(self, item: EvaluationCase) -> EvaluationResult:
        return EvaluationResult(
            evaluation_case_id="SOMEONE-ELSE",
            category=item.category.value,
            status=EvaluationStatus.PASSED,
        )


async def test_routes_each_case_to_its_category_evaluator() -> None:
    runner = EvaluationRunner({EvaluationCategory.GOLDEN: PassingEvaluator()})
    results = await runner.run([case("A", EvaluationCategory.GOLDEN)])

    assert [(r.evaluation_case_id, r.status) for r in results] == [("A", EvaluationStatus.PASSED)]


async def test_unregistered_category_is_an_error_not_a_skip() -> None:
    runner = EvaluationRunner({})
    results = await runner.run([case("A", EvaluationCategory.FOLLOW_UP)])

    assert results[0].status is EvaluationStatus.ERROR
    assert "no evaluator registered for category follow_up" in results[0].findings[0]


async def test_one_failing_case_does_not_stop_the_run() -> None:
    runner = EvaluationRunner(
        {
            EvaluationCategory.GOLDEN: RaisingEvaluator(),
            EvaluationCategory.FOLLOW_UP: PassingEvaluator(),
        }
    )
    results = await runner.run(
        [case("A", EvaluationCategory.GOLDEN), case("B", EvaluationCategory.FOLLOW_UP)]
    )

    assert [r.status for r in results] == [EvaluationStatus.ERROR, EvaluationStatus.PASSED]
    assert "RuntimeError: database unavailable" in results[0].findings[0]


async def test_result_for_a_different_case_is_an_error() -> None:
    runner = EvaluationRunner({EvaluationCategory.GOLDEN: WrongCaseEvaluator()})
    results = await runner.run([case("A", EvaluationCategory.GOLDEN)])

    assert results[0].status is EvaluationStatus.ERROR
    assert results[0].evaluation_case_id == "A"


async def test_unavailable_evaluator_reports_its_reason() -> None:
    result = await UnavailableEvaluator("not wired").evaluate(
        case("A", EvaluationCategory.AMBIGUITY)
    )

    assert result.status is EvaluationStatus.ERROR
    assert result.findings == ("not wired",)


@pytest.mark.parametrize("category", list(EvaluationCategory))
async def test_every_category_yields_exactly_one_result(category: EvaluationCategory) -> None:
    results = await EvaluationRunner({}).run([case("A", category)])

    assert len(results) == 1
