"""Run the 105-case evaluation suite as a LangSmith experiment.

The cases become a LangSmith dataset (created once, then only missing cases are added).
Each experiment runs every case through the same evaluators as the local harness and
scores it in LangSmith, so runs can be compared over time.

    uv run --env-file .env python -m supplyguard.evaluation.langsmith_experiment

Needs the local stack (PostgreSQL and Neo4j with the support data loaded) and
LANGSMITH_API_KEY in .env. The external-risk MCP is optional: when it is not running, the
impact report only carries a warning. Only the synthetic evaluation cases are uploaded.
"""

import argparse
import asyncio
import os
import sys
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from langsmith import Client, aevaluate
from langsmith.schemas import Example

from supplyguard.contracts.events import EvaluationResult, EvaluationStatus
from supplyguard.evaluation.cases import EvaluationCase, load_cases, require_full_suite
from supplyguard.evaluation.report import build_report, render_markdown
from supplyguard.evaluation.runner import EvaluationRunner

DATASET_NAME = "supplyguard-eval-105"
DATASET_DESCRIPTION = "SupplyGuard AI held-out evaluation suite (105 synthetic cases)."
DEFAULT_EXPERIMENT_PREFIX = "supplyguard-eval"
DEFAULT_RISK_MCP_URL = "http://127.0.0.1:8101/mcp"

Target = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


def example_rows(cases: Sequence[EvaluationCase]) -> list[dict[str, Any]]:
    """One LangSmith example per case: the message as input, the expectation as reference."""
    return [
        {
            "inputs": {
                "case_id": case.id,
                "category": case.category.value,
                "message": case.message,
                "depends_on": case.depends_on,
            },
            "reference_outputs": {"expected": case.expected},
            "metadata": {"category": case.category.value},
        }
        for case in cases
    ]


def _dataset_examples(
    examples: Sequence[Example], rows: dict[str, dict[str, Any]], name: str
) -> dict[str, Example]:
    by_id: dict[str, Example] = {}
    for example in examples:
        inputs = example.inputs or {}
        case_id = inputs.get("case_id")
        row = rows.get(case_id) if isinstance(case_id, str) else None
        if (
            row is None
            or case_id in by_id
            or inputs != row["inputs"]
            or example.outputs != row["reference_outputs"]
        ):
            raise ValueError(
                f"LangSmith dataset {name!r} contains an unexpected, duplicate or changed example. "
                "Use --dataset with a new name for a revised suite."
            )
        by_id[case_id] = example
    return by_id


def sync_dataset(
    client: Client, cases: Sequence[EvaluationCase], name: str = DATASET_NAME
) -> tuple[Example, ...]:
    """Read the exact suite version and add only its missing cases."""
    rows = {row["inputs"]["case_id"]: row for row in example_rows(cases)}
    if client.has_dataset(dataset_name=name):
        dataset = client.read_dataset(dataset_name=name)
        present = _dataset_examples(list(client.list_examples(dataset_id=dataset.id)), rows, name)
    else:
        dataset = client.create_dataset(name, description=DATASET_DESCRIPTION)
        present = {}
    missing = [row for case_id, row in rows.items() if case_id not in present]
    if missing:
        client.create_examples(dataset_id=dataset.id, examples=missing)
        present = _dataset_examples(list(client.list_examples(dataset_id=dataset.id)), rows, name)
    if len(present) != len(rows):
        raise RuntimeError(f"LangSmith dataset {name!r} is missing cases after synchronization.")
    return tuple(present[case_id] for case_id in rows)


def result_outputs(result: EvaluationResult) -> dict[str, Any]:
    # Findings can include exception details or evidence text. Keep them in the local report.
    return {
        "status": result.status.value,
        "metrics": dict(result.metrics),
    }


def make_target(
    runner: EvaluationRunner,
    cases: Sequence[EvaluationCase],
    results: dict[str, EvaluationResult] | None = None,
) -> Target:
    """The function LangSmith calls for each example: run that case through the harness."""
    by_id: Mapping[str, EvaluationCase] = {case.id: case for case in cases}

    async def run_case(inputs: dict[str, Any]) -> dict[str, Any]:
        case = by_id[inputs["case_id"]]
        [result] = await runner.run([case])
        if results is not None:
            results[case.id] = result
        return result_outputs(result)

    return run_case


def passed(outputs: dict[str, Any]) -> dict[str, Any]:
    """1 when the case passed. Failures and errors both score 0."""
    return {"key": "passed", "score": int(outputs["status"] == EvaluationStatus.PASSED.value)}


def not_runnable(outputs: dict[str, Any]) -> dict[str, Any]:
    """1 when the case could not run (component not wired yet), so it is never hidden."""
    return {"key": "not_runnable", "score": int(outputs["status"] == EvaluationStatus.ERROR.value)}


def first_errors(results: Sequence[EvaluationResult]) -> list[str]:
    """One example error per category, so a wiring problem is visible without opening LangSmith."""
    seen: dict[str, str] = {}
    for result in results:
        if result.status is EvaluationStatus.ERROR and result.category not in seen:
            seen[result.category] = result.findings[0] if result.findings else "no detail"
    return [f"{category} error: {finding}" for category, finding in sorted(seen.items())]


async def run_experiment(
    client: Client,
    runner: EvaluationRunner,
    cases: Sequence[EvaluationCase],
    *,
    experiment_prefix: str = DEFAULT_EXPERIMENT_PREFIX,
    dataset_name: str = DATASET_NAME,
) -> tuple[Any, tuple[EvaluationResult, ...]]:
    examples = sync_dataset(client, cases, dataset_name)
    collected: dict[str, EvaluationResult] = {}
    experiment = await aevaluate(
        make_target(runner, cases, collected),
        data=examples,
        evaluators=[passed, not_runnable],
        experiment_prefix=experiment_prefix,
        description="SupplyGuard AI evaluation harness on the 105-case suite.",
        metadata={"cases": len(cases)},
        max_concurrency=1,
        client=client,
    )
    ordered = tuple(collected[case.id] for case in cases if case.id in collected)
    return experiment, ordered


async def _main(experiment_prefix: str, dataset_name: str) -> int:
    from supplyguard.approval.policy import load_approval_policy
    from supplyguard.calculations.service import DeterministicCostService
    from supplyguard.data.database import Database
    from supplyguard.evaluation.evaluators import default_evaluators, load_golden_figures
    from supplyguard.external_risk.mcp_client import McpRiskClient
    from supplyguard.external_risk.service import ExternalRiskService
    from supplyguard.graph.client import DependencyGraph
    from supplyguard.retrieval.adapters import GuardrailContentSafetyService
    from supplyguard.workflow.runtime import ConnectionPerCallIntelligence
    from supplyguard.workflow.settings import DATA_DIR

    cases = load_cases(DATA_DIR / "evaluation" / "eval_cases.jsonl").cases
    require_full_suite(cases)

    database = Database(os.environ["DATABASE_URL"])
    graph = DependencyGraph.connect(
        os.environ["NEO4J_URI"], os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"]
    )
    try:
        intelligence = ConnectionPerCallIntelligence(
            database,
            graph,
            ExternalRiskService(
                McpRiskClient(os.getenv("EXTERNAL_RISK_MCP_URL") or DEFAULT_RISK_MCP_URL)
            ),
            DeterministicCostService(),
        )
        runner = EvaluationRunner(
            default_evaluators(
                intelligence,
                GuardrailContentSafetyService(),
                load_golden_figures(DATA_DIR / "evaluation" / "golden_scenario.json"),
                load_approval_policy(DATA_DIR / "config" / "policies.json"),
            )
        )
        experiment, results = await run_experiment(
            Client(), runner, cases, experiment_prefix=experiment_prefix, dataset_name=dataset_name
        )
    finally:
        await graph.close()
        await database.close()

    report = build_report(cases, results)
    print(render_markdown(report))
    for line in first_errors(results):
        print(line)
    print(f"\nLangSmith experiment: {experiment.experiment_name}")
    return int(bool(report.missing_case_ids))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the evaluation suite in LangSmith")
    parser.add_argument("--prefix", default=DEFAULT_EXPERIMENT_PREFIX)
    parser.add_argument("--dataset", default=DATASET_NAME)
    arguments = parser.parse_args()
    if not os.getenv("LANGSMITH_API_KEY"):
        sys.exit("LANGSMITH_API_KEY is not set. Add it to .env and use: uv run --env-file .env")
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    sys.exit(asyncio.run(_main(arguments.prefix, arguments.dataset), loop_factory=loop_factory))


if __name__ == "__main__":
    main()
