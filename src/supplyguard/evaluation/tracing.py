"""Optional per-case tracing. LangSmith is used only when an API key is configured."""

import os
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from typing import Protocol

from langsmith import trace as langsmith_trace

from supplyguard.evaluation.cases import EvaluationCase

DEFAULT_PROJECT = "supplyguard-evaluation"


class CaseTracer(Protocol):
    def span(self, case: EvaluationCase) -> AbstractContextManager[str | None]: ...


class NullTracer:
    @contextmanager
    def span(self, case: EvaluationCase) -> Iterator[None]:
        yield None


class LangSmithTracer:
    def __init__(self, project_name: str = DEFAULT_PROJECT) -> None:
        self.project_name = project_name

    @contextmanager
    def span(self, case: EvaluationCase) -> Iterator[str]:
        with langsmith_trace(
            name=f"evaluation:{case.id}",
            run_type="chain",
            project_name=self.project_name,
            inputs={"case_id": case.id, "category": case.category.value},
        ) as run:
            yield str(run.id)


def build_tracer() -> CaseTracer:
    """Use LangSmith when LANGSMITH_API_KEY is set; otherwise record no trace ids."""
    if os.getenv("LANGSMITH_API_KEY"):
        return LangSmithTracer(os.getenv("LANGSMITH_PROJECT", DEFAULT_PROJECT))
    return NullTracer()
