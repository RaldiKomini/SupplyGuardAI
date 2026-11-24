"""Application boundary between Chainlit and the case workflow.

Every workflow call passes through `CaseService`, which checks case ownership before
the workflow reads or changes case state. The service holds no state, so a restart
rebuilds it from the same workflow and the same configuration.

Case access is owner-only. The one exception is an approval request: a user whose role is
listed in the request's `required_approver_roles` may read that request and submit their own
decision on it, and nothing else about the case.
"""

from collections.abc import AsyncIterator
from typing import Protocol

from supplyguard.contracts import AuthorizationContext, CaseSummary, IncidentInput, UIEvent
from supplyguard.contracts.planning import ApprovalDecision, ApprovalRequest
from supplyguard.contracts.protocols import CaseWorkflowService


class CaseNotFoundError(LookupError):
    """Raised for a missing case and for another user's case alike, so existence is not revealed."""


class RequestRejectedError(ValueError):
    """Raised when a request does not match the authenticated user."""


class ApprovalRequestSource(Protocol):
    async def get_request(self, approval_request_id: str) -> ApprovalRequest | None: ...


class CaseService:
    def __init__(
        self, workflow: CaseWorkflowService, approvals: ApprovalRequestSource | None = None
    ) -> None:
        self._workflow = workflow
        # Without a source, only case owners can approve: the second-approver path stays closed.
        self._approvals = approvals

    async def list_cases(self, authorization: AuthorizationContext) -> tuple[CaseSummary, ...]:
        # Filtering here protects users even if a workflow implementation returns extra cases.
        return tuple(
            case
            for case in await self._workflow.list_cases(authorization)
            if case.user_id == authorization.user_id
        )

    async def start_case(
        self, incident: IncidentInput, authorization: AuthorizationContext
    ) -> AsyncIterator[UIEvent]:
        if incident.user_id != authorization.user_id:
            raise RequestRejectedError("incident does not belong to the authenticated user")
        return self._workflow.start_case(incident, authorization)

    async def send_message(
        self, case_id: str, message: str, authorization: AuthorizationContext
    ) -> AsyncIterator[UIEvent]:
        if not message.strip():
            raise RequestRejectedError("message must not be blank")
        await self._require_owned(case_id, authorization)
        return self._workflow.send_message(case_id, message, authorization)

    async def resume_case(
        self, case_id: str, authorization: AuthorizationContext
    ) -> AsyncIterator[UIEvent]:
        await self._require_owned(case_id, authorization)
        return self._workflow.resume_case(case_id, authorization)

    async def submit_approval(
        self, decision: ApprovalDecision, authorization: AuthorizationContext
    ) -> AsyncIterator[UIEvent]:
        # Each approver submits only their own decision, so one user cannot supply another's.
        if decision.approver_user_ids != (authorization.user_id,) or decision.approver_roles != (
            authorization.role,
        ):
            raise RequestRejectedError("approval must name only the authenticated user and role")
        request = await self._find_request(decision.approval_request_id)
        owns_case = await self._owns(decision.case_id, authorization)
        designated = (
            request is not None
            and request.case_id == decision.case_id
            and authorization.role in request.required_approver_roles
        )
        if not (owns_case or designated):
            raise CaseNotFoundError(decision.case_id)
        if self._approvals is not None and (
            request is None
            or request.case_id != decision.case_id
            or request.payload_hash != decision.payload_hash
        ):
            raise RequestRejectedError("decision does not match the approval request")
        return self._workflow.submit_approval(decision, authorization)

    async def get_approval_request(
        self, approval_request_id: str, authorization: AuthorizationContext
    ) -> ApprovalRequest:
        request = await self._find_request(approval_request_id)
        if request is None or not (
            authorization.role in request.required_approver_roles
            or await self._owns(request.case_id, authorization)
        ):
            raise CaseNotFoundError(approval_request_id)
        return request

    async def _find_request(self, approval_request_id: str) -> ApprovalRequest | None:
        if self._approvals is None:
            return None
        return await self._approvals.get_request(approval_request_id)

    async def _owns(self, case_id: str, authorization: AuthorizationContext) -> bool:
        return case_id in {case.case_id for case in await self.list_cases(authorization)}

    async def _require_owned(self, case_id: str, authorization: AuthorizationContext) -> None:
        if not await self._owns(case_id, authorization):
            raise CaseNotFoundError(case_id)
