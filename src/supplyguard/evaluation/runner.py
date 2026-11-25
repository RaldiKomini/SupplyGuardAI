"""Runs each evaluation case through the evaluator registered for its category."""

from collections.abc import Mapping, Sequence
from typing import Protocol

from supplyguard.contracts.events import EvaluationResult, EvaluationStatus
from supplyguard.evaluation.cases import EvaluationCase, EvaluationCategory


class CaseEvaluator(Protocol):
    async def evaluate(self, case: EvaluationCase) -> EvaluationResult: ...


class UnavailableEvaluator:
    """Records a case whose component is not yet wired, so it is never silently skipped."""

    def __init__(self, reason: str) -> None:
        self.reason = reason

    async def evaluate(self, case: EvaluationCase) -> EvaluationResult:
        return _error(case, self.reason)


class EvaluationRunner:
    def __init__(self, evaluators: Mapping[EvaluationCategory, CaseEvaluator]) -> None:
        self.evaluators = dict(evaluators)

    async def run(self, cases: Sequence[EvaluationCase]) -> tuple[EvaluationResult, ...]:
        results: list[EvaluationResult] = []
        for case in cases:
            evaluator = self.evaluators.get(case.category) or UnavailableEvaluator(
                f"no evaluator registered for category {case.category.value}"
            )
            results.append(await self._run_one(case, evaluator))
        return tuple(results)

    async def _run_one(self, case: EvaluationCase, evaluator: CaseEvaluator) -> EvaluationResult:
        try:
            result = await evaluator.evaluate(case)
        except Exception as error:
            return _error(case, f"{type(error).__name__}: {error}")
        if result.evaluation_case_id != case.id or result.category != case.category.value:
            return _error(case, "evaluator returned a result for a different case")
        return result


def _error(case: EvaluationCase, reason: str) -> EvaluationResult:
    return EvaluationResult(
        evaluation_case_id=case.id,
        category=case.category.value,
        status=EvaluationStatus.ERROR,
        findings=(reason,),
    )
