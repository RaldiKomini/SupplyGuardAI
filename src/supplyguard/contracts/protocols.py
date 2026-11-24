"""Structural protocols that keep component implementations independent."""

from typing import Protocol

from supplyguard.contracts.evidence import EvidenceBundle
from supplyguard.contracts.impact import OperationalIntelligenceResult
from supplyguard.contracts.incident import ConfirmedIncident
from supplyguard.contracts.planning import (
    ApprovalDecision,
    ApprovalRequest,
    ExecutionResult,
    RecoveryPlan,
)


class OperationalIntelligenceService(Protocol):
    async def investigate(self, incident: ConfirmedIncident) -> OperationalIntelligenceResult: ...


class EvidenceService(Protocol):
    async def retrieve(
        self, incident: ConfirmedIncident, access_scopes: tuple[str, ...]
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
