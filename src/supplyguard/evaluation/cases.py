"""Loading and validation of the held-out evaluation cases."""

import json
from collections.abc import Sequence
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import Field, model_validator

from supplyguard.contracts.common import ContractModel

EXPECTED_CASE_COUNT = 105


class EvaluationCategory(StrEnum):
    GOLDEN = "golden"
    EXTRACTION_AND_IMPACT = "extraction_and_impact"
    AMBIGUITY = "ambiguity"
    PROMPT_INJECTION = "prompt_injection"
    FOLLOW_UP = "follow_up"
    RESTART_RECOVERY = "restart_recovery"
    POLICY_AND_AUTHORIZATION = "policy_and_authorization"


class EvaluationCase(ContractModel):
    id: str = Field(min_length=1)
    category: EvaluationCategory
    message: str = Field(min_length=1)
    expected: dict[str, Any] = Field(default_factory=dict)
    depends_on: str | None = None


class EvaluationCaseSet(ContractModel):
    cases: tuple[EvaluationCase, ...]

    @model_validator(mode="after")
    def _check_integrity(self) -> "EvaluationCaseSet":
        ids = [case.id for case in self.cases]
        if len(ids) != len(set(ids)):
            duplicates = sorted({case_id for case_id in ids if ids.count(case_id) > 1})
            raise ValueError(f"duplicate evaluation case IDs: {duplicates}")
        known = set(ids)
        for case in self.cases:
            if case.depends_on is not None and case.depends_on not in known:
                raise ValueError(f"{case.id} depends on unknown case {case.depends_on}")
        return self

    def __len__(self) -> int:
        return len(self.cases)

    def by_category(self) -> dict[EvaluationCategory, tuple[EvaluationCase, ...]]:
        grouped: dict[EvaluationCategory, list[EvaluationCase]] = {}
        for case in self.cases:
            grouped.setdefault(case.category, []).append(case)
        return {category: tuple(cases) for category, cases in grouped.items()}


def load_cases(path: Path) -> EvaluationCaseSet:
    """Read a JSONL file with one evaluation case per line."""
    rows: list[EvaluationCase] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            rows.append(EvaluationCase.model_validate(json.loads(line)))
        except (json.JSONDecodeError, ValueError) as error:
            raise ValueError(f"{path}:{line_number}: {error}") from error
    return EvaluationCaseSet(cases=tuple(rows))


def require_full_suite(cases: Sequence[EvaluationCase]) -> None:
    """Fail loudly if the supplied suite is not the 105-case suite."""
    if len(cases) != EXPECTED_CASE_COUNT:
        raise ValueError(f"expected {EXPECTED_CASE_COUNT} cases, found {len(cases)}")
