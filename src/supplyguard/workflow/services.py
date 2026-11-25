"""The other components the workflow calls, all through the shared protocols."""

from dataclasses import dataclass

from supplyguard.contracts.protocols import (
    ApprovalService,
    EvidenceService,
    MasterDataService,
    OperationalIntelligenceService,
    ProcurementExecutionService,
    RecoveryPlanningService,
    ResolutionMemoryStore,
)


@dataclass(frozen=True)
class WorkflowServices:
    master_data: MasterDataService  # Raldi: supplier/component lookup for intake
    intelligence: OperationalIntelligenceService  # Raldi: impact, dependencies, external risk
    evidence: EvidenceService  # George: documents, conflicts, security findings
    planning: RecoveryPlanningService  # Athanasia: options, recommendation, challenge findings
    approval: ApprovalService  # Athanasia: exact payload, decision rules, revalidation
    execution: ProcurementExecutionService  # Athanasia: idempotent procurement writes
    memory: ResolutionMemoryStore  # George: approved resolution summaries for future cases
