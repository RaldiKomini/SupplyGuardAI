import pytest

from supplyguard.contracts.events import EvaluationResult, EvaluationStatus
from supplyguard.evaluation.cases import EvaluationCase, EvaluationCategory
from supplyguard.evaluation.report import build_report


def result(
    case_id: str, category: EvaluationCategory, status: EvaluationStatus
) -> EvaluationResult:
    return EvaluationResult(evaluation_case_id=case_id, category=category.value, status=status)


def test_report_marks_missing_results_as_not_passed() -> None:
    cases = [
        EvaluationCase(id="A", category=EvaluationCategory.GOLDEN, message="m"),
        EvaluationCase(id="B", category=EvaluationCategory.GOLDEN, message="m"),
    ]
    results = [result("A", EvaluationCategory.GOLDEN, EvaluationStatus.PASSED)]

    report = build_report(cases, results)

    assert report.missing_case_ids == ("B",)
    assert report.overall_passed is False


def test_report_passes_only_when_every_case_passed() -> None:
    cases = [EvaluationCase(id="A", category=EvaluationCategory.GOLDEN, message="m")]
    results = [result("A", EvaluationCategory.GOLDEN, EvaluationStatus.PASSED)]

    assert build_report(cases, results).overall_passed is True


def test_report_rejects_results_for_unknown_or_duplicate_cases() -> None:
    cases = [EvaluationCase(id="A", category=EvaluationCategory.GOLDEN, message="m")]
    passed = result("A", EvaluationCategory.GOLDEN, EvaluationStatus.PASSED)

    with pytest.raises(ValueError, match="unknown case Z"):
        build_report(cases, [result("Z", EvaluationCategory.GOLDEN, EvaluationStatus.PASSED)])
    with pytest.raises(ValueError, match="duplicate result"):
        build_report(cases, [passed, passed])
