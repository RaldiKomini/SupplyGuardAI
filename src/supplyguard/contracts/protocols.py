"""Structural protocols that keep component implementations independent."""

from collections.abc import AsyncIterator, Sequence
from typing import Protocol

from supplyguard.contracts.application import AuthorizationContext, CaseSummary
from supplyguard.contracts.common import SecurityFinding
from supplyguard.contracts.events import UIEvent
from supplyguard.contracts.evidence import EvidenceBundle
from supplyguard.contracts.impact import (
    CalculationTrace,
    LatePenaltyCostInput,
    OpenPurchaseOrder,
    OperationalIntelligenceResult,
)
from supplyguard.contracts.incident import (
    ComponentCandidate,
    ConfirmedIncident,
    IncidentInput,
    SupplierCandidate,
)
from supplyguard.contracts.memory import ResolutionMemory
from supplyguard.contracts.planning import (
    ApprovalDecision,
    ApprovalRequest,
    ExecutionResult,
    RecoveryAction,
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


class MasterDataService(Protocol):
    """Supplier and component records used to resolve the user's wording to IDs."""

    async def find_suppliers(self, query: str) -> tuple[SupplierCandidate, ...]: ...

    async def find_components(self, query: str) -> tuple[ComponentCandidate, ...]: ...

    async def suppliers_of(self, component_id: str) -> tuple[SupplierCandidate, ...]: ...


class OperationalIntelligenceService(Protocol):
    async def investigate(self, incident: ConfirmedIncident) -> OperationalIntelligenceResult: ...


class OperationalCostService(Protocol):
    def estimate_expedite(self, purchase_order: OpenPurchaseOrder) -> CalculationTrace: ...

    def estimate_late_penalty(self, cost_input: LatePenaltyCostInput) -> CalculationTrace: ...


class EvidenceService(Protocol):
    async def retrieve(
        self, incident: ConfirmedIncident, authorization: AuthorizationContext
    ) -> EvidenceBundle: ...


class ContentSafetyService(Protocol):
    """Finds embedded instructions in untrusted text, e.g. a notice pasted into a user message."""

    def scan(self, text: str, source_id: str) -> tuple[SecurityFinding, ...]: ...


class ResolutionMemoryStore(Protocol):
    async def save(self, memory: ResolutionMemory) -> None: ...

    async def search(
        self, query: str, authorization: AuthorizationContext, limit: int = 3
    ) -> tuple[ResolutionMemory, ...]: ...


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

    async def revalidate(
        self, request: ApprovalRequest, modified_actions: tuple[RecoveryAction, ...]
    ) -> ApprovalRequest: ...


class ProcurementExecutionService(Protocol):
    async def execute(
        self, request: ApprovalRequest, decision: ApprovalDecision
    ) -> tuple[ExecutionResult, ...]: ...
