"""Execute approved recovery actions exactly once against a simulated ERP.

Nothing runs without a decision that matches the exact approved payload.
Every action gets a stable idempotency key: a retry returns the stored outcome
instead of writing again.
"""

from datetime import datetime
from typing import Protocol

from supplyguard.approval.requests import decision_problems
from supplyguard.contracts import (
    ApprovalDecision,
    ApprovalRequest,
    ExecutionResult,
    ExecutionStatus,
    RecoveryAction,
    RecoveryActionType,
)

# The simulated ERP prefix for each action type it can carry out.
SIMULATED_ERP = {
    RecoveryActionType.PURCHASE_ALTERNATIVE: "SIM-PO",
    RecoveryActionType.EXPEDITE_ORDER: "SIM-EXP",
    RecoveryActionType.TRANSFER_INVENTORY: "SIM-TR",
}


class ExecutionStore(Protocol):
    """Where execution outcomes are kept (in memory for tests, PostgreSQL in the app)."""

    async def get(self, idempotency_key: str) -> ExecutionResult | None: ...

    async def claim(
        self, idempotency_key: str, request: ApprovalRequest, action: RecoveryAction
    ) -> bool: ...

    async def complete(self, result: ExecutionResult) -> None: ...


def idempotency_key(request: ApprovalRequest, action: RecoveryAction) -> str:
    """Stable key: the same approved action always gets the same key."""
    return f"{request.case_id}:{request.approval_request_id}:{action.action_id}"


async def execute_approved(
    request: ApprovalRequest,
    decision: ApprovalDecision,
    store: ExecutionStore,
    *,
    now: datetime,
) -> tuple[ExecutionResult, ...]:
    """Run each approved action once and return one result per action."""
    problems = decision_problems(request, decision)
    if problems:
        message = "; ".join(problems)
        return tuple(
            _result(request, action, ExecutionStatus.REJECTED, message)
            for action in request.actions
        )
    return tuple([await _execute_one(request, action, store, now) for action in request.actions])


async def _execute_one(
    request: ApprovalRequest, action: RecoveryAction, store: ExecutionStore, now: datetime
) -> ExecutionResult:
    key = idempotency_key(request, action)
    stored = await store.get(key)
    if stored is not None:
        # A replay returns the stored outcome; the store itself is not changed.
        return stored.model_copy(update={"status": ExecutionStatus.ALREADY_EXECUTED})
    if action.action_type not in SIMULATED_ERP:
        return _result(
            request,
            action,
            ExecutionStatus.FAILED,
            f"{action.action_type} is not a procurement write",
        )
    if not await store.claim(key, request, action):
        return _result(
            request,
            action,
            ExecutionStatus.ALREADY_EXECUTED,
            "another run is executing this action",
        )
    reference = f"{SIMULATED_ERP[action.action_type]}-{request.case_id}-{action.action_id}"
    result = _result(
        request,
        action,
        ExecutionStatus.SUCCEEDED,
        "executed in the simulated ERP",
        executed_at=now,
        external_reference=reference,
    )
    await store.complete(result)
    return result


def rejected_results(request: ApprovalRequest, message: str) -> tuple[ExecutionResult, ...]:
    """One rejected result per action, for a request that must not run at all."""
    rejected = ExecutionStatus.REJECTED
    return tuple(_result(request, action, rejected, message) for action in request.actions)


def _result(
    request: ApprovalRequest,
    action: RecoveryAction,
    status: ExecutionStatus,
    message: str,
    *,
    executed_at: datetime | None = None,
    external_reference: str | None = None,
) -> ExecutionResult:
    return ExecutionResult(
        case_id=request.case_id,
        approval_request_id=request.approval_request_id,
        action_id=action.action_id,
        action_type=action.action_type,
        idempotency_key=idempotency_key(request, action),
        status=status,
        message=message,
        executed_at=executed_at,
        external_reference=external_reference,
    )


class InMemoryExecutionStore:
    """Execution store for tests and the offline demo."""

    def __init__(self) -> None:
        self.claimed: set[str] = set()
        self.results: dict[str, ExecutionResult] = {}

    async def get(self, idempotency_key: str) -> ExecutionResult | None:
        return self.results.get(idempotency_key)

    async def claim(
        self, idempotency_key: str, request: ApprovalRequest, action: RecoveryAction
    ) -> bool:
        if idempotency_key in self.claimed:
            return False
        self.claimed.add(idempotency_key)
        return True

    async def complete(self, result: ExecutionResult) -> None:
        self.results[result.idempotency_key] = result
