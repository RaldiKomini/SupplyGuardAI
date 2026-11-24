from supplyguard.contracts.events import EvaluationResult, EvaluationStatus
from supplyguard.evaluation.cases import EvaluationCase, EvaluationCategory
from supplyguard.evaluation.report import build_report, render_markdown


def _case(
    case_id: str, category: EvaluationCategory = EvaluationCategory.AMBIGUITY
) -> EvaluationCase:
    return EvaluationCase(id=case_id, category=category, message="m")


def _result(
    case_id: str, status: EvaluationStatus, category: str = "ambiguity"
) -> EvaluationResult:
    return EvaluationResult(evaluation_case_id=case_id, category=category, status=status)


def test_all_passed_gives_overall_pass() -> None:
    cases = (_case("EV-1"), _case("EV-2"))
    results = (_result("EV-1", EvaluationStatus.PASSED), _result("EV-2", EvaluationStatus.PASSED))

    report = build_report(cases, results)

    assert report.overall_passed
    assert report.categories[0].passed == 2


def test_one_failure_fails_the_overall_verdict() -> None:
    cases = (_case("EV-1"), _case("EV-2"))
    results = (_result("EV-1", EvaluationStatus.PASSED), _result("EV-2", EvaluationStatus.FAILED))

    report = build_report(cases, results)

    assert not report.overall_passed
    assert report.categories[0].failed == 1


def test_error_result_fails_the_overall_verdict() -> None:
    cases = (_case("EV-1"),)

    report = build_report(cases, (_result("EV-1", EvaluationStatus.ERROR),))

    assert not report.overall_passed
    assert report.categories[0].errored == 1


def test_missing_result_is_reported_and_fails_the_verdict() -> None:
    cases = (_case("EV-1"), _case("EV-2"))

    report = build_report(cases, (_result("EV-1", EvaluationStatus.PASSED),))

    assert report.missing_case_ids == ("EV-2",)
    assert not report.overall_passed


def test_categories_are_counted_separately() -> None:
    cases = (
        _case("EV-1", EvaluationCategory.GOLDEN),
        _case("EV-2", EvaluationCategory.PROMPT_INJECTION),
    )
    results = (
        _result("EV-1", EvaluationStatus.PASSED, "golden"),
        _result("EV-2", EvaluationStatus.FAILED, "prompt_injection"),
    )

    report = build_report(cases, results)

    by_name = {item.category: item for item in report.categories}
    assert by_name["golden"].passed == 1
    assert by_name["prompt_injection"].failed == 1


def test_markdown_shows_verdict_and_category_rows() -> None:
    cases = (_case("EV-1"),)

    text = render_markdown(build_report(cases, (_result("EV-1", EvaluationStatus.FAILED),)))

    assert text.startswith("# Evaluation report: NOT PASSED")
    assert "| ambiguity | 1 | 0 | 1 | 0 |" in text
