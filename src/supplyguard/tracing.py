"""LangSmith tracing for the recovery, approval and procurement service calls.

Tracing is controlled by the environment (LANGSMITH_TRACING, LANGSMITH_API_KEY,
LANGSMITH_PROJECT in .env). When it is off, the decorator does nothing, so tests
and offline runs need no key.
"""

from collections.abc import Callable
from typing import Any, TypeVar

from langsmith import traceable

F = TypeVar("F", bound=Callable[..., Any])


def _without_self(inputs: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in inputs.items() if key != "self"}


def traced(name: str, *, component: str) -> Callable[[F], F]:
    """Trace one service call as a LangSmith run named `name`, tagged with its component."""
    return traceable(
        name=name,
        run_type="chain",
        metadata={"component": component},
        tags=[component],
        process_inputs=_without_self,
    )
