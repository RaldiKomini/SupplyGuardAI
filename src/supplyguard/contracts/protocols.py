"""Structural protocols that keep component implementations independent."""

from collections.abc import AsyncIterator, Sequence
from typing import Protocol

from supplyguard.contracts.application import AuthorizationContext, CaseSummary
from supplyguard.contracts.events import UIEvent
from supplyguard.contracts.evidence import EvidenceBundle
from supplyguard.contracts.impact import OperationalIntelligenceResult
from supplyguard.contracts.incident import ConfirmedIncident, IncidentInput
from supplyguard.contracts.planning import (
    ApprovalDecision,
    ApprovalRequest,
    ExecutionResult,
    RecoveryPlan,
)


class CaseWorkflowService(Protocol):
    """Workflow entry points called by the application layer.

    Implementations must reject a case whose owner differs from `authorization.user_id`.
    """

    def start_case(
        self, incident: IncidentInput, authorization: AuthorizationContext
    ) -> AsyncIterator[UIEvent]: ...

    def send_message(
        self, case_id: str, message: str, authorization: AuthorizationContext
    ) -> AsyncIterator[UIEvent]: ...

    def resume_case(
        self, case_id: str, authorization: AuthorizationContext
    ) -> AsyncIterator[UIEvent]: ...

    def submit_approval(
        self, decision: ApprovalDecision, authorization: AuthorizationContext
    ) -> AsyncIterator[UIEvent]: ...

    async def list_cases(self, authorization: AuthorizationContext) -> Sequence[CaseSummary]: ...


class OperationalIntelligenceService(Protocol):
    async def investigate(self, incident: ConfirmedIncident) -> OperationalIntelligenceResult: ...


class EvidenceService(Protocol):
    async def retrieve(
        self, incident: ConfirmedIncident, authorization: AuthorizationContext
    ) -> EvidenceBundle: ...


class RecoveryPlanningService(Protocol):
    async def propose(
        self,
        incident: ConfirmedIncident,
        intelligence: OperationalIntelligenceResult,
        evidence: EvidenceBundle,
    ) -> RecoveryPlan: ...


class ApprovalService(Protocol):
    async def create_request(self, plan: RecoveryPlan) -> ApprovalRequest: ...

    async def record_decision(
        self, request: ApprovalRequest, decision: ApprovalDecision
    ) -> ApprovalDecision: ...


class ProcurementExecutionService(Protocol):
    async def execute(
        self, request: ApprovalRequest, decision: ApprovalDecision
    ) -> ExecutionResult: ...
