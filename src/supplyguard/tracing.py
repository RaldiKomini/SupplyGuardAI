"""LangSmith tracing for the recovery, approval and procurement service calls.

Tracing is controlled by the environment (LANGSMITH_TRACING, LANGSMITH_API_KEY,
LANGSMITH_PROJECT in .env). When it is off, the decorator does nothing, so tests
and offline runs need no key.

Only an allowlist of safe fields is sent: identifiers, roles, statuses, counts, costs and
dates. Free text (messages, evidence content, reasons, action payloads) never leaves the
process, so confidential documents cannot reach LangSmith through a trace.
"""

from collections.abc import Callable
from datetime import date
from decimal import Decimal
from typing import Any, TypeVar

from langsmith import traceable

from supplyguard.contracts import (
    ApprovalDecision,
    ApprovalRequest,
    ConfirmedIncident,
    EvidenceBundle,
    ExecutionResult,
    RecoveryAction,
    RecoveryPlan,
)
from supplyguard.contracts.impact import OperationalIntelligenceResult

F = TypeVar("F", bound=Callable[..., Any])


def _action(action: RecoveryAction) -> dict[str, Any]:
    return {
        "action_id": action.action_id,
        "action_type": action.action_type.value,
        "cost_eur": action.cost_eur,
    }


def trace_view(value: Any) -> Any:
    """The part of a value that may be sent to LangSmith. Anything unknown is left out."""
    if value is None or isinstance(value, bool | int | Decimal | date):
        return value
    if isinstance(value, tuple | list):
        return [trace_view(item) for item in value]
    if isinstance(value, RecoveryAction):
        return _action(value)
    if isinstance(value, ConfirmedIncident):
        return {
            "case_id": value.case_id,
            "supplier_id": value.supplier_id,
            "component_id": value.component_id,
            "delay_days": value.delay_days,
            "spending_cap_eur": value.spending_cap_eur,
        }
    if isinstance(value, OperationalIntelligenceResult):
        return {"case_id": value.impact.case_id, "component_id": value.impact.component_id}
    if isinstance(value, EvidenceBundle):
        return {
            "case_id": value.case_id,
            "evidence_ids": [item.evidence_id for item in value.items],
            "security_findings": len(value.security_findings),
        }
    if isinstance(value, RecoveryPlan):
        return {
            "case_id": value.case_id,
            "recommended_option_id": value.recommended_option_id,
            "needs_revision": value.needs_revision,
            "options": [
                {
                    "option_id": option.option_id,
                    "recovery_cost_eur": option.recovery_cost_eur,
                    "uncovered_units": option.uncovered_units,
                }
                for option in value.options
            ],
            "challenge_checks": [finding.check for finding in value.challenge_findings],
        }
    if isinstance(value, ApprovalRequest):
        return {
            "approval_request_id": value.approval_request_id,
            "case_id": value.case_id,
            "option_id": value.option_id,
            "payload_version": value.payload_version,
            "payload_hash": value.payload_hash,
            "total_cost_eur": value.total_cost_eur,
            "required_approver_roles": list(value.required_approver_roles),
            "actions": [_action(action) for action in value.actions],
        }
    if isinstance(value, ApprovalDecision):
        return {
            "approval_request_id": value.approval_request_id,
            "case_id": value.case_id,
            "payload_hash": value.payload_hash,
            "approved": value.approved,
            "is_modification": value.is_modification,
            "approver_user_ids": list(value.approver_user_ids),
            "approver_roles": list(value.approver_roles),
            "modified_actions": [_action(action) for action in value.modified_actions],
        }
    if isinstance(value, ExecutionResult):
        return {
            "action_id": value.action_id,
            "action_type": value.action_type.value,
            "status": value.status.value,
            "idempotency_key": value.idempotency_key,
            "external_reference": value.external_reference,
        }
    return {"omitted": type(value).__name__}


def _safe_inputs(inputs: dict[str, Any]) -> dict[str, Any]:
    return {key: trace_view(value) for key, value in inputs.items() if key != "self"}


def _safe_outputs(output: Any) -> dict[str, Any]:
    return {"output": trace_view(output)}


def traced(name: str, *, component: str) -> Callable[[F], F]:
    """Trace one service call as a LangSmith run named `name`, tagged with its component."""
    return traceable(
        name=name,
        run_type="chain",
        metadata={"component": component},
        tags=[component],
        process_inputs=_safe_inputs,
        process_outputs=_safe_outputs,
    )
