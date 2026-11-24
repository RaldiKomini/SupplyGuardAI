import asyncio

from supplyguard.contracts.events import EvaluationResult, EvaluationStatus
from supplyguard.evaluation.cases import EvaluationCase, EvaluationCategory
from supplyguard.evaluation.runner import EvaluationRunner


def _case(case_id: str, category: EvaluationCategory) -> EvaluationCase:
    return EvaluationCase(id=case_id, category=category, message="m")


class PassingEvaluator:
    async def evaluate(self, case: EvaluationCase) -> EvaluationResult:
        return EvaluationResult(
            evaluation_case_id=case.id,
            category=case.category.value,
            status=EvaluationStatus.PASSED,
        )


class RaisingEvaluator:
    async def evaluate(self, case: EvaluationCase) -> EvaluationResult:
        raise RuntimeError("component down")


class WrongIdEvaluator:
    async def evaluate(self, case: EvaluationCase) -> EvaluationResult:
        return EvaluationResult(
            evaluation_case_id="EV-OTHER",
            category=case.category.value,
            status=EvaluationStatus.PASSED,
        )


def _run(
    runner: EvaluationRunner, cases: tuple[EvaluationCase, ...]
) -> tuple[EvaluationResult, ...]:
    return asyncio.run(runner.run(cases))


def test_every_case_gets_exactly_one_result() -> None:
    cases = (
        _case("EV-1", EvaluationCategory.GOLDEN),
        _case("EV-2", EvaluationCategory.AMBIGUITY),
    )
    runner = EvaluationRunner({category: PassingEvaluator() for category in EvaluationCategory})

    results = _run(runner, cases)

    assert [result.evaluation_case_id for result in results] == ["EV-1", "EV-2"]
    assert all(result.status is EvaluationStatus.PASSED for result in results)


def test_category_without_adapter_is_recorded_as_error_not_skipped() -> None:
    cases = (_case("EV-1", EvaluationCategory.PROMPT_INJECTION),)

    (result,) = _run(EvaluationRunner({}), cases)

    assert result.status is EvaluationStatus.ERROR
    assert result.evaluation_case_id == "EV-1"
    assert "prompt_injection" in result.findings[0]


def test_failing_component_is_recorded_and_suite_continues() -> None:
    cases = (
        _case("EV-1", EvaluationCategory.GOLDEN),
        _case("EV-2", EvaluationCategory.AMBIGUITY),
    )
    runner = EvaluationRunner(
        {
            EvaluationCategory.GOLDEN: RaisingEvaluator(),
            EvaluationCategory.AMBIGUITY: PassingEvaluator(),
        }
    )

    results = _run(runner, cases)

    assert results[0].status is EvaluationStatus.ERROR
    assert "component down" in results[0].findings[0]
    assert results[1].status is EvaluationStatus.PASSED


def test_result_naming_another_case_is_recorded_as_error() -> None:
    cases = (_case("EV-1", EvaluationCategory.GOLDEN),)
    runner = EvaluationRunner({EvaluationCategory.GOLDEN: WrongIdEvaluator()})

    (result,) = _run(runner, cases)

    assert result.status is EvaluationStatus.ERROR
    assert result.evaluation_case_id == "EV-1"
