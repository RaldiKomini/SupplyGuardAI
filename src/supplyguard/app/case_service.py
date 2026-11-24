"""Application boundary between Chainlit and the case workflow.

Every workflow call passes through `CaseService`, which checks case ownership before
the workflow reads or changes case state. The service holds no state, so a restart
rebuilds it from the same workflow and the same configuration.
"""

from collections.abc import AsyncIterator

from supplyguard.contracts import AuthorizationContext, CaseSummary, IncidentInput, UIEvent
from supplyguard.contracts.planning import ApprovalDecision
from supplyguard.contracts.protocols import CaseWorkflowService


class CaseNotFoundError(LookupError):
    """Raised for a missing case and for another user's case alike, so existence is not revealed."""


class RequestRejectedError(ValueError):
    """Raised when a request does not match the authenticated user."""


class CaseService:
    def __init__(self, workflow: CaseWorkflowService) -> None:
        self._workflow = workflow

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
        await self._require_owned(decision.case_id, authorization)
        if authorization.user_id not in decision.approver_user_ids:
            raise RequestRejectedError("approval must name the authenticated user as an approver")
        return self._workflow.submit_approval(decision, authorization)

    async def _require_owned(self, case_id: str, authorization: AuthorizationContext) -> None:
        owned_ids = {case.case_id for case in await self.list_cases(authorization)}
        if case_id not in owned_ids:
            raise CaseNotFoundError(case_id)
