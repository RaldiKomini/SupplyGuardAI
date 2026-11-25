from pathlib import Path

import pytest

from supplyguard.evaluation.cases import (
    EXPECTED_CASE_COUNT,
    EvaluationCategory,
    load_cases,
    require_full_suite,
)

SUPPORT = Path(__file__).resolve().parents[2] / "support-data" / "evaluation"


def test_supplied_suite_has_all_105_cases_in_every_category() -> None:
    suite = load_cases(SUPPORT / "eval_cases.jsonl")

    require_full_suite(suite.cases)
    counts = {category: len(cases) for category, cases in suite.by_category().items()}
    assert len(suite) == EXPECTED_CASE_COUNT
    assert counts == {
        EvaluationCategory.EXTRACTION_AND_IMPACT: 64,
        EvaluationCategory.AMBIGUITY: 10,
        EvaluationCategory.PROMPT_INJECTION: 10,
        EvaluationCategory.POLICY_AND_AUTHORIZATION: 10,
        EvaluationCategory.RESTART_RECOVERY: 6,
        EvaluationCategory.FOLLOW_UP: 4,
        EvaluationCategory.GOLDEN: 1,
    }


def test_loader_reports_the_line_of_an_invalid_case(tmp_path: Path) -> None:
    path = tmp_path / "cases.jsonl"
    path.write_text(
        '{"id": "A", "category": "golden", "message": "m"}\n'
        '{"id": "B", "category": "not_a_category", "message": "m"}\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=r"cases.jsonl:2"):
        load_cases(path)


def test_loader_rejects_duplicate_ids(tmp_path: Path) -> None:
    path = tmp_path / "cases.jsonl"
    row = '{"id": "A", "category": "golden", "message": "m"}\n'
    path.write_text(row * 2, encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate evaluation case IDs"):
        load_cases(path)


def test_loader_rejects_dependency_on_unknown_case(tmp_path: Path) -> None:
    path = tmp_path / "cases.jsonl"
    path.write_text(
        '{"id": "A", "category": "follow_up", "message": "m", "depends_on": "Z"}\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="depends on unknown case Z"):
        load_cases(path)


def test_require_full_suite_rejects_partial_suite() -> None:
    suite = load_cases(SUPPORT / "eval_cases.jsonl")

    with pytest.raises(ValueError, match="expected 105 cases"):
        require_full_suite(suite.cases[:10])
