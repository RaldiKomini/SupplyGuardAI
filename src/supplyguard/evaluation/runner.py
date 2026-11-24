"""Runs evaluation cases and records one result for every case."""

from collections.abc import Mapping
from typing import Protocol

from supplyguard.contracts.events import EvaluationResult, EvaluationStatus
from supplyguard.evaluation.cases import EvaluationCase, EvaluationCategory


class CaseEvaluator(Protocol):
    """Component-owned adapter that judges one case against its expected behaviour."""

    async def evaluate(self, case: EvaluationCase) -> EvaluationResult: ...


class UnavailableEvaluator:
    """Recorded for categories whose component adapter is not wired yet."""

    def __init__(self, category: EvaluationCategory) -> None:
        self._category = category

    async def evaluate(self, case: EvaluationCase) -> EvaluationResult:
        return EvaluationResult(
            evaluation_case_id=case.id,
            category=case.category.value,
            status=EvaluationStatus.ERROR,
            findings=(f"no evaluator registered for category {self._category.value}",),
        )


class EvaluationRunner:
    def __init__(self, evaluators: Mapping[EvaluationCategory, CaseEvaluator]) -> None:
        self._evaluators = dict(evaluators)

    async def run(self, cases: tuple[EvaluationCase, ...]) -> tuple[EvaluationResult, ...]:
        results: list[EvaluationResult] = []
        for case in cases:
            results.append(await self._run_one(case))
        return tuple(results)

    async def _run_one(self, case: EvaluationCase) -> EvaluationResult:
        evaluator = self._evaluators.get(case.category)
        if evaluator is None:
            evaluator = UnavailableEvaluator(case.category)
        try:
            result = await evaluator.evaluate(case)
        except Exception as error:  # a failing component must not stop the suite
            return self._error(case, f"{type(error).__name__}: {error}")
        if result.evaluation_case_id != case.id:
            return self._error(case, f"evaluator returned result for {result.evaluation_case_id}")
        return result

    @staticmethod
    def _error(case: EvaluationCase, finding: str) -> EvaluationResult:
        return EvaluationResult(
            evaluation_case_id=case.id,
            category=case.category.value,
            status=EvaluationStatus.ERROR,
            findings=(finding,),
        )
