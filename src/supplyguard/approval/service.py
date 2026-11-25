"""ApprovalService for the workflow: requests, decisions and revalidation, all stored.

Each approver's decision is stored separately. A tier that needs two roles is satisfied
only when two different users have approved the same request.
"""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from decimal import Decimal

from supplyguard.approval.policy import ApprovalPolicy
from supplyguard.approval.requests import build_approval_request, revalidate_request
from supplyguard.contracts import (
    ApprovalDecision,
    ApprovalRequest,
    ConfirmedIncident,
    ImpactReport,
    RecoveryAction,
    RecoveryPlan,
)
from supplyguard.contracts.protocols import OperationalCostService
from supplyguard.planning.options import PlanningInputs
from supplyguard.procurement.approvals import ApprovalRecordStore, DecisionRecord
from supplyguard.tracing import traced

# The workflow tells the service which incident and impact report a case was planned on.
CaseContext = Callable[[str], Awaitable[tuple[ConfirmedIncident, ImpactReport]]]


class DeterministicApprovalService:
    """Implements ApprovalService on top of the approval records."""

    def __init__(
        self,
        records: ApprovalRecordStore,
        policy: ApprovalPolicy,
        *,
        case_context: CaseContext,
        costs: OperationalCostService,
        transfer_cost_eur_per_unit: Decimal,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.records = records
        self.policy = policy
        self.case_context = case_context
        self.costs = costs
        self.transfer_cost_eur_per_unit = transfer_cost_eur_per_unit
        self.clock = clock

    @traced("approval.create_request", component="approval")
    async def create_request(self, plan: RecoveryPlan) -> ApprovalRequest:
        """Store an approval request for the recommended option of a plan that passed review."""
        if plan.needs_revision:
            raise ValueError("the plan has blocking challenger findings and needs revision")
        option = next((o for o in plan.options if o.option_id == plan.recommended_option_id), None)
        if option is None:
            raise ValueError("the plan has no recommended option to approve")
        version = await self.records.latest_version(plan.case_id) + 1
        request = build_approval_request(
            case_id=plan.case_id,
            option=option,
            policy=self.policy,
            requested_at=self.clock(),
            payload_version=version,
        )
        await self.records.save_request(request)
        return request

    @traced("approval.record_decision", component="approval")
    async def record_decision(
        self, request: ApprovalRequest, decision: ApprovalDecision
    ) -> ApprovalDecision:
        """Store one approver's decision and return the combined decision on the request.

        The caller (the application layer) has authenticated the user and their role.
        """
        if decision.is_modification:
            raise ValueError("an edit must be recorded together with its revised request")
        stored_request = await self._request_for_decision(request, decision)
        if not decision.approver_user_ids:
            raise ValueError("a decision must name the user who made it")
        roles = decision.approver_roles or ("",) * len(decision.approver_user_ids)
        if len(roles) != len(decision.approver_user_ids):
            raise ValueError("every approver needs exactly one role")
        if decision.approved:
            unexpected = set(roles) - set(stored_request.required_approver_roles)
            if unexpected:
                raise ValueError(f"role not required for this request: {sorted(unexpected)}")

        kind = "approve" if decision.approved else "reject"
        for user_id, role in zip(decision.approver_user_ids, roles, strict=True):
            record = DecisionRecord(user_id, role, kind, decision.decided_at)
            await self.records.save_decision(stored_request.approval_request_id, record)
        _, combined = await self.records.load(stored_request.approval_request_id)
        return combined

    @traced("approval.record_modification", component="approval")
    async def record_modification(
        self, request: ApprovalRequest, decision: ApprovalDecision
    ) -> ApprovalRequest:
        stored = await self._request_for_decision(request, decision)
        if not decision.is_modification:
            raise ValueError("the decision must propose an edit")
        if len(decision.approver_user_ids) != 1 or len(decision.approver_roles) != 1:
            raise ValueError("an edit must name exactly one approver and role")
        role = decision.approver_roles[0]
        if role not in stored.required_approver_roles:
            raise ValueError(f"role not required for this request: {role}")
        revision = await self._build_revision(stored, decision.modified_actions)
        record = DecisionRecord(decision.approver_user_ids[0], role, "modify", decision.decided_at)
        return await self.records.save_revision(stored.approval_request_id, record, revision)

    async def _request_for_decision(
        self, request: ApprovalRequest, decision: ApprovalDecision
    ) -> ApprovalRequest:
        stored = await self.records.load(request.approval_request_id)
        if stored is None:
            raise ValueError(f"unknown approval request {request.approval_request_id}")
        stored_request, _ = stored
        if (
            decision.approval_request_id != stored_request.approval_request_id
            or decision.case_id != stored_request.case_id
            or decision.payload_hash != stored_request.payload_hash
        ):
            raise ValueError("the decision does not match the stored request and payload")
        return stored_request

    @traced("approval.revalidate", component="approval")
    async def revalidate(
        self, request: ApprovalRequest, modified_actions: tuple[RecoveryAction, ...]
    ) -> ApprovalRequest:
        """Check and re-price an edit; store it as a new request that needs fresh approval."""
        if await self.records.latest_version(request.case_id) > request.payload_version:
            raise ValueError("a newer approval request exists for this case; edit that one")
        new_request = await self._build_revision(request, modified_actions)
        await self.records.save_request(new_request)
        return new_request

    async def _build_revision(
        self, request: ApprovalRequest, modified_actions: tuple[RecoveryAction, ...]
    ) -> ApprovalRequest:
        incident, impact = await self.case_context(request.case_id)
        inputs = PlanningInputs(
            incident=incident,
            impact=impact,
            costs=self.costs,
            transfer_cost_eur_per_unit=self.transfer_cost_eur_per_unit,
        )
        return revalidate_request(
            request, modified_actions, inputs=inputs, policy=self.policy, requested_at=self.clock()
        )
