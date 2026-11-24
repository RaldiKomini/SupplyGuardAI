"""Summarises recorded evaluation results into the overall pass/fail verdict."""

from collections import Counter
from collections.abc import Sequence

from pydantic import Field

from supplyguard.contracts.common import ContractModel
from supplyguard.contracts.events import EvaluationResult, EvaluationStatus
from supplyguard.evaluation.cases import EvaluationCase


class CategorySummary(ContractModel):
    category: str
    total: int
    passed: int
    failed: int
    errored: int


class EvaluationReport(ContractModel):
    total_cases: int
    recorded_results: int
    missing_case_ids: tuple[str, ...] = ()
    categories: tuple[CategorySummary, ...] = Field(default_factory=tuple)

    @property
    def overall_passed(self) -> bool:
        return (
            not self.missing_case_ids
            and self.recorded_results == self.total_cases
            and all(item.failed == 0 and item.errored == 0 for item in self.categories)
        )


def build_report(
    cases: Sequence[EvaluationCase], results: Sequence[EvaluationResult]
) -> EvaluationReport:
    """Every case must have exactly one result; a missing result counts as not passed."""
    result_by_case: dict[str, EvaluationResult] = {}
    for result in results:
        result_by_case.setdefault(result.evaluation_case_id, result)
    missing = tuple(case.id for case in cases if case.id not in result_by_case)

    categories: dict[str, Counter[EvaluationStatus]] = {}
    for case in cases:
        result = result_by_case.get(case.id)
        status = result.status if result is not None else EvaluationStatus.ERROR
        categories.setdefault(case.category.value, Counter())[status] += 1

    summaries = tuple(
        CategorySummary(
            category=category,
            total=sum(counts.values()),
            passed=counts[EvaluationStatus.PASSED],
            failed=counts[EvaluationStatus.FAILED],
            errored=counts[EvaluationStatus.ERROR],
        )
        for category, counts in categories.items()
    )
    return EvaluationReport(
        total_cases=len(cases),
        recorded_results=len(result_by_case),
        missing_case_ids=missing,
        categories=summaries,
    )


def render_markdown(report: EvaluationReport) -> str:
    verdict = "PASSED" if report.overall_passed else "NOT PASSED"
    lines = [
        f"# Evaluation report: {verdict}",
        "",
        f"Cases: {report.total_cases}; recorded results: {report.recorded_results}; "
        f"missing: {len(report.missing_case_ids)}",
        "",
        "| Category | Total | Passed | Failed | Error |",
        "|---|---|---|---|---|",
    ]
    lines += [
        f"| {item.category} | {item.total} | {item.passed} | {item.failed} | {item.errored} |"
        for item in report.categories
    ]
    return "\n".join(lines) + "\n"
