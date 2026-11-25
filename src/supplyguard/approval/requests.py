"""Build approval requests and check decisions against the exact payload."""

from datetime import datetime
from decimal import Decimal

from supplyguard.approval.hashing import payload_hash
from supplyguard.approval.policy import ApprovalPolicy, required_approver_roles
from supplyguard.approval.revalidation import RevalidationError, reprice_actions
from supplyguard.contracts import (
    ApprovalDecision,
    ApprovalRequest,
    RecoveryAction,
    RecoveryOption,
)
from supplyguard.planning.options import PlanningInputs


def build_approval_request(
    *,
    case_id: str,
    option: RecoveryOption,
    policy: ApprovalPolicy,
    requested_at: datetime,
    payload_version: int = 1,
) -> ApprovalRequest:
    """Turn the recommended option into a request bound to its exact actions."""
    return _request(
        case_id=case_id,
        option_id=option.option_id,
        actions=option.actions,
        evidence_source_ids=tuple(source.source_id for source in option.sources),
        policy=policy,
        requested_at=requested_at,
        payload_version=payload_version,
    )


def revalidate_request(
    request: ApprovalRequest,
    modified_actions: tuple[RecoveryAction, ...],
    *,
    inputs: PlanningInputs,
    policy: ApprovalPolicy,
    requested_at: datetime,
) -> ApprovalRequest:
    """Check and re-price a modified payload; it becomes a new request needing a fresh decision.

    Raises RevalidationError when an edited action breaks a rule (capacity, certification,
    stock, spending cap). Stated costs are ignored: every action is priced again.
    """
    actions, problems = reprice_actions(modified_actions, inputs)
    if problems:
        raise RevalidationError(problems)
    return _request(
        case_id=request.case_id,
        option_id=request.option_id,
        actions=actions,
        evidence_source_ids=request.evidence_source_ids,
        policy=policy,
        requested_at=requested_at,
        payload_version=request.payload_version + 1,
    )


def decision_problems(request: ApprovalRequest, decision: ApprovalDecision) -> tuple[str, ...]:
    """Reasons the decision does not authorise execution (empty means authorised)."""
    problems: list[str] = []
    if not decision.approved:
        problems.append("the request was not approved")
    if decision.approval_request_id != request.approval_request_id:
        problems.append("the decision refers to a different approval request")
    if decision.case_id != request.case_id:
        problems.append("the decision refers to a different case")
    if decision.payload_hash != request.payload_hash:
        problems.append("the decision was made for a different payload")
    if payload_hash(request.actions) != request.payload_hash:
        problems.append("the request actions no longer match their payload hash")
    missing = set(request.required_approver_roles) - set(decision.approver_roles)
    if missing:
        problems.append(f"missing approval from: {', '.join(sorted(missing))}")
    return tuple(problems)


def _request(
    *,
    case_id: str,
    option_id: str,
    actions: tuple[RecoveryAction, ...],
    evidence_source_ids: tuple[str, ...],
    policy: ApprovalPolicy,
    requested_at: datetime,
    payload_version: int,
) -> ApprovalRequest:
    if not actions:
        raise ValueError("an approval request needs at least one action")
    total = sum((action.cost_eur for action in actions), Decimal("0"))
    digest = payload_hash(actions)
    return ApprovalRequest(
        approval_request_id=f"APR-{case_id}-v{payload_version}",
        case_id=case_id,
        option_id=option_id,
        payload_version=payload_version,
        payload_hash=digest,
        actions=actions,
        total_cost_eur=total,
        required_approver_roles=required_approver_roles(total, policy),
        evidence_source_ids=evidence_source_ids,
        requested_at=requested_at,
    )
