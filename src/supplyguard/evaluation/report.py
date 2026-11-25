"""Per-category summary and overall verdict for an evaluation run."""

from collections.abc import Sequence

from pydantic import Field

from supplyguard.contracts.common import ContractModel
from supplyguard.contracts.events import EvaluationResult, EvaluationStatus
from supplyguard.evaluation.cases import EvaluationCase


class CategorySummary(ContractModel):
    category: str
    total: int = Field(ge=0)
    passed: int = Field(ge=0)
    failed: int = Field(ge=0)
    errored: int = Field(ge=0)


class EvaluationReport(ContractModel):
    total_cases: int = Field(ge=0)
    recorded_results: int = Field(ge=0)
    missing_case_ids: tuple[str, ...] = ()
    categories: tuple[CategorySummary, ...]

    @property
    def overall_passed(self) -> bool:
        return (
            not self.missing_case_ids
            and self.recorded_results == self.total_cases
            and all(summary.passed == summary.total for summary in self.categories)
        )


def build_report(
    cases: Sequence[EvaluationCase], results: Sequence[EvaluationResult]
) -> EvaluationReport:
    case_ids = {case.id for case in cases}
    by_id: dict[str, EvaluationResult] = {}
    for result in results:
        if result.evaluation_case_id not in case_ids:
            raise ValueError(f"result for unknown case {result.evaluation_case_id}")
        if result.evaluation_case_id in by_id:
            raise ValueError(f"duplicate result for case {result.evaluation_case_id}")
        by_id[result.evaluation_case_id] = result

    counts: dict[str, dict[str, int]] = {}
    for case in cases:
        bucket = counts.setdefault(
            case.category.value, {"total": 0, "passed": 0, "failed": 0, "errored": 0}
        )
        bucket["total"] += 1
        result = by_id.get(case.id)
        if result is None:
            continue
        if result.status is EvaluationStatus.PASSED:
            bucket["passed"] += 1
        elif result.status is EvaluationStatus.FAILED:
            bucket["failed"] += 1
        else:
            bucket["errored"] += 1

    return EvaluationReport(
        total_cases=len(cases),
        recorded_results=len(by_id),
        missing_case_ids=tuple(case.id for case in cases if case.id not in by_id),
        categories=tuple(
            CategorySummary(category=name, **bucket) for name, bucket in sorted(counts.items())
        ),
    )


def render_markdown(report: EvaluationReport) -> str:
    lines = [
        "| Category | Total | Passed | Failed | Errored |",
        "|---|---:|---:|---:|---:|",
    ]
    for summary in report.categories:
        lines.append(
            f"| {summary.category} | {summary.total} | {summary.passed} | "
            f"{summary.failed} | {summary.errored} |"
        )
    verdict = "PASSED" if report.overall_passed else "NOT PASSED"
    lines.append("")
    lines.append(
        f"Overall: **{verdict}** ({report.recorded_results}/{report.total_cases} results recorded)"
    )
    if report.missing_case_ids:
        lines.append(f"Missing results: {', '.join(report.missing_case_ids)}")
    return "\n".join(lines)
