"""The LangSmith experiment wraps the local harness without changing its verdicts."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from langsmith import Client

from supplyguard.contracts.events import EvaluationResult, EvaluationStatus
from supplyguard.evaluation import langsmith_experiment as experiment
from supplyguard.evaluation.cases import EvaluationCase, EvaluationCategory
from supplyguard.evaluation.runner import EvaluationRunner

pytestmark = pytest.mark.asyncio


def case(case_id: str, category=EvaluationCategory.GOLDEN) -> EvaluationCase:
    return EvaluationCase(
        id=case_id, category=category, message=f"message {case_id}", expected={"x": 1}
    )


class StatusEvaluator:
    def __init__(self, status: EvaluationStatus) -> None:
        self.status = status

    async def evaluate(self, item: EvaluationCase) -> EvaluationResult:
        return EvaluationResult(
            evaluation_case_id=item.id,
            category=item.category.value,
            status=self.status,
            metrics={"score": 1.0},
            findings=("checked",),
        )


def runner() -> EvaluationRunner:
    return EvaluationRunner(
        {
            EvaluationCategory.GOLDEN: StatusEvaluator(EvaluationStatus.PASSED),
            EvaluationCategory.AMBIGUITY: StatusEvaluator(EvaluationStatus.FAILED),
        }
    )


CASES = (case("EV-1"), case("EV-2", EvaluationCategory.AMBIGUITY), case("EV-3"))


async def test_every_case_becomes_one_example_with_its_expectation() -> None:
    rows = experiment.example_rows(CASES)

    assert [row["inputs"]["case_id"] for row in rows] == ["EV-1", "EV-2", "EV-3"]
    assert rows[1]["inputs"]["category"] == "ambiguity"
    assert rows[0]["outputs"] == {"expected": {"x": 1}}
    assert rows[1]["metadata"] == {"category": "ambiguity"}


async def test_a_new_dataset_gets_every_case() -> None:
    client = MagicMock(spec=Client)
    client.has_dataset.return_value = False
    client.create_dataset.return_value = SimpleNamespace(id="ds-1")

    experiment.sync_dataset(client, CASES)

    client.create_dataset.assert_called_once()
    examples = client.create_examples.call_args.kwargs["examples"]
    assert client.create_examples.call_args.kwargs["dataset_id"] == "ds-1"
    assert len(examples) == 3


async def test_an_existing_dataset_only_gets_missing_cases() -> None:
    client = MagicMock(spec=Client)
    client.has_dataset.return_value = True
    client.read_dataset.return_value = SimpleNamespace(id="ds-1")
    client.list_examples.return_value = [
        SimpleNamespace(inputs={"case_id": "EV-1"}),
        SimpleNamespace(inputs={"case_id": "EV-2"}),
    ]

    experiment.sync_dataset(client, CASES)

    client.create_dataset.assert_not_called()
    examples = client.create_examples.call_args.kwargs["examples"]
    assert [row["inputs"]["case_id"] for row in examples] == ["EV-3"]


async def test_a_complete_dataset_is_left_unchanged() -> None:
    client = MagicMock(spec=Client)
    client.has_dataset.return_value = True
    client.read_dataset.return_value = SimpleNamespace(id="ds-1")
    client.list_examples.return_value = [SimpleNamespace(inputs={"case_id": c.id}) for c in CASES]

    experiment.sync_dataset(client, CASES)

    client.create_examples.assert_not_called()


async def test_the_target_runs_the_case_through_the_harness() -> None:
    target = experiment.make_target(runner(), CASES)

    outputs = await target({"case_id": "EV-2"})

    assert outputs == {"status": "failed", "metrics": {"score": 1.0}, "findings": ["checked"]}


async def test_scores_keep_failures_and_unrunnable_cases_visible() -> None:
    assert experiment.passed({"status": "passed"})["score"] == 1
    assert experiment.passed({"status": "failed"})["score"] == 0
    assert experiment.passed({"status": "error"})["score"] == 0
    assert experiment.not_runnable({"status": "error"}) == {"key": "not_runnable", "score": 1}
    assert experiment.not_runnable({"status": "failed"})["score"] == 0


async def test_the_experiment_reports_the_same_verdicts_as_the_harness(monkeypatch) -> None:
    calls = {}

    async def fake_aevaluate(target, *, data, evaluators, **kwargs):
        calls.update(data=data, kwargs=kwargs)
        scores = []
        for row in experiment.example_rows(CASES):
            outputs = await target(row["inputs"])
            scores.append([evaluate(outputs) for evaluate in evaluators])
        calls["scores"] = scores
        return SimpleNamespace(experiment_name="supplyguard-eval-1")

    monkeypatch.setattr(experiment, "aevaluate", fake_aevaluate)
    client = MagicMock(spec=Client)
    client.has_dataset.return_value = False
    client.create_dataset.return_value = SimpleNamespace(id="ds-1")

    result, results = await experiment.run_experiment(client, runner(), CASES)

    assert result.experiment_name == "supplyguard-eval-1"
    assert calls["data"] == experiment.DATASET_NAME
    assert calls["kwargs"]["client"] is client
    assert [r.evaluation_case_id for r in results] == ["EV-1", "EV-2", "EV-3"]
    assert [r.status for r in results] == [
        EvaluationStatus.PASSED,
        EvaluationStatus.FAILED,
        EvaluationStatus.PASSED,
    ]
    assert [s[0]["score"] for s in calls["scores"]] == [1, 0, 1]


async def test_one_error_per_category_is_shown() -> None:
    def error(case_id: str, category: str, finding: str) -> EvaluationResult:
        return EvaluationResult(
            evaluation_case_id=case_id,
            category=category,
            status=EvaluationStatus.ERROR,
            findings=(finding,),
        )

    lines = experiment.first_errors(
        [
            error("EV-1", "golden", "OSError: connection refused"),
            error("EV-2", "golden", "OSError: connection refused again"),
            error("EV-3", "ambiguity", "not wired"),
        ]
    )

    assert lines == ["ambiguity error: not wired", "golden error: OSError: connection refused"]
