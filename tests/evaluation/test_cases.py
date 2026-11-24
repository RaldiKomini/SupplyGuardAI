import json
from pathlib import Path

import pytest

from supplyguard.evaluation.cases import (
    EvaluationCaseSet,
    EvaluationCategory,
    load_cases,
    require_full_suite,
)

ROOT = Path(__file__).resolve().parents[2]
SUITE_PATH = ROOT / "support-data" / "evaluation" / "eval_cases.jsonl"


def _row(case_id: str, category: str = "ambiguity", **extra: object) -> dict[str, object]:
    return {"id": case_id, "category": category, "message": "m", **extra}


def _write(path: Path, rows: list[dict[str, object]]) -> Path:
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    return path


def test_supplied_suite_has_105_cases_in_known_categories() -> None:
    cases = load_cases(SUITE_PATH)

    require_full_suite(cases.cases)
    assert len(cases) == 105
    assert set(cases.by_category()) <= set(EvaluationCategory)
    assert cases.by_category()[EvaluationCategory.GOLDEN][0].id == "EV-0001"


def test_supplied_follow_up_and_recovery_cases_depend_on_existing_cases() -> None:
    cases = load_cases(SUITE_PATH)
    ids = {case.id for case in cases.cases}

    dependent = [case for case in cases.cases if case.depends_on is not None]
    assert dependent
    assert all(case.depends_on in ids for case in dependent)


def test_load_rejects_duplicate_case_ids(tmp_path: Path) -> None:
    path = _write(tmp_path / "cases.jsonl", [_row("EV-1"), _row("EV-1")])

    with pytest.raises(ValueError, match="duplicate evaluation case IDs"):
        load_cases(path)


def test_load_rejects_dependency_on_unknown_case(tmp_path: Path) -> None:
    path = _write(tmp_path / "cases.jsonl", [_row("EV-2", depends_on="EV-404")])

    with pytest.raises(ValueError, match="depends on unknown case EV-404"):
        load_cases(path)


def test_load_rejects_unknown_category(tmp_path: Path) -> None:
    path = _write(tmp_path / "cases.jsonl", [_row("EV-3", category="made_up")])

    with pytest.raises(ValueError, match="EV-3|made_up"):
        load_cases(path)


def test_load_reports_malformed_line_number(tmp_path: Path) -> None:
    path = tmp_path / "cases.jsonl"
    path.write_text(json.dumps(_row("EV-4")) + "\n{not json\n", encoding="utf-8")

    with pytest.raises(ValueError, match=r"cases\.jsonl:2"):
        load_cases(path)


def test_load_rejects_unexpected_fields(tmp_path: Path) -> None:
    path = _write(tmp_path / "cases.jsonl", [_row("EV-5", surprise=True)])

    with pytest.raises(ValueError):
        load_cases(path)


def test_full_suite_check_rejects_wrong_count() -> None:
    cases = load_cases(SUITE_PATH).cases

    with pytest.raises(ValueError, match="expected 105 cases, found 104"):
        require_full_suite(cases[:-1])


def test_empty_set_is_valid_but_is_not_the_full_suite() -> None:
    empty = EvaluationCaseSet(cases=())

    assert len(empty) == 0
    with pytest.raises(ValueError):
        require_full_suite(empty.cases)
