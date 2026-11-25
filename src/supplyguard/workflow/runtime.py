"""Builds the case workflow from the real components (central workflow integration).

    use_windows_compatible_event_loop()      # once, at the top of the app module
    async with open_case_workflow() as workflow:
        ...                                   # e.g. CaseService(workflow, ...)

    async with open_workflow_parts() as parts:   # evaluation harness
        evaluators.update(parts.evaluators(owner, approvers))

Every component comes from its owner's package and is used only through its protocol. The
recipe follows the owners' integration tests. Settings come from the environment
(.env.example): DATABASE_URL, NEO4J_URI/USER/PASSWORD, PROCUREMENT_MCP_URL,
EXTERNAL_RISK_MCP_URL, EVIDENCE_BACKEND (memory, pgvector or chroma; default pgvector), and
the model settings read by make_model().
"""

import os
from collections.abc import AsyncIterator, Sequence
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from decimal import Decimal

from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver

from supplyguard.approval.policy import load_approval_policy
from supplyguard.approval.service import DeterministicApprovalService
from supplyguard.calculations.service import DeterministicCostService
from supplyguard.contracts import (
    AuthorizationContext,
    ConfirmedIncident,
    OperationalIntelligenceResult,
)
from supplyguard.data.database import Database
from supplyguard.evaluation.cases import EvaluationCategory
from supplyguard.external_risk.mcp_client import McpRiskClient
from supplyguard.external_risk.service import ExternalRiskService
from supplyguard.graph.client import DependencyGraph
from supplyguard.impact import DefaultOperationalIntelligenceService
from supplyguard.llm import make_model
from supplyguard.planning.service import DeterministicRecoveryPlanningService
from supplyguard.procurement.approvals import PostgresApprovalRecords
from supplyguard.procurement.mcp_client import McpProcurementClient
from supplyguard.repositories import OperationalRepository, PostgresMasterDataService
from supplyguard.retrieval.adapters import (
    EvidenceResolutionMemoryStore,
    GuardrailContentSafetyService,
)
from supplyguard.retrieval.service import EvidenceService
from supplyguard.workflow.checkpoints import postgres_checkpointer
from supplyguard.workflow.evaluation import (
    AmbiguityEvaluator,
    FollowUpEvaluator,
    RestartEvaluator,
)
from supplyguard.workflow.service import CaseWorkflow
from supplyguard.workflow.services import WorkflowServices
from supplyguard.workflow.settings import DATA_DIR, policies

POLICIES_FILE = DATA_DIR / "config" / "policies.json"


class ConnectionPerCallIntelligence:
    """OperationalIntelligenceService on a fresh database connection for every case, since
    the operational repository works on one open connection."""

    def __init__(
        self, database: Database, graph: DependencyGraph, risk: ExternalRiskService, costs
    ) -> None:
        self._database, self._graph, self._risk, self._costs = database, graph, risk, costs

    async def investigate(self, incident: ConfirmedIncident) -> OperationalIntelligenceResult:
        async with self._database.connection() as connection:
            service = DefaultOperationalIntelligenceService(
                OperationalRepository(connection), self._graph, self._risk, self._costs
            )
            return await service.investigate(incident)


@dataclass(frozen=True)
class WorkflowParts:
    """The real model, services and checkpoints. Every workflow built from them shares the
    checkpoints, so a second workflow on the same parts is what an application restart is."""

    model: BaseChatModel
    services: WorkflowServices
    checkpointer: BaseCheckpointSaver

    def new_workflow(self, checkpointer: BaseCheckpointSaver | None = None) -> CaseWorkflow:
        return CaseWorkflow(
            model=self.model, services=self.services, checkpointer=checkpointer or self.checkpointer
        )

    def evaluators(
        self, owner: AuthorizationContext, approvers: Sequence[AuthorizationContext]
    ) -> dict[EvaluationCategory, object]:
        """The workflow's harness evaluators on the real components."""

        def shared() -> BaseCheckpointSaver:  # the approval service reads cases from here
            return self.checkpointer

        return {
            EvaluationCategory.AMBIGUITY: AmbiguityEvaluator(self.model, self.services.master_data),
            EvaluationCategory.FOLLOW_UP: FollowUpEvaluator(
                self.new_workflow, owner, approvers, shared
            ),
            EvaluationCategory.RESTART_RECOVERY: RestartEvaluator(
                self.new_workflow, owner, approvers, shared
            ),
        }


@asynccontextmanager
async def open_case_workflow(model: BaseChatModel | None = None) -> AsyncIterator[CaseWorkflow]:
    """The workflow the application uses."""
    async with open_workflow_parts(model) as parts:
        yield parts.new_workflow()


@asynccontextmanager
async def open_workflow_parts(model: BaseChatModel | None = None) -> AsyncIterator[WorkflowParts]:
    database_url = os.environ["DATABASE_URL"]
    async with AsyncExitStack() as stack:
        database = Database(database_url)
        stack.push_async_callback(database.close)
        graph = DependencyGraph.connect(
            os.environ["NEO4J_URI"], os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"]
        )
        stack.push_async_callback(graph.close)

        costs = DeterministicCostService()
        evidence = EvidenceService.from_support_data(
            DATA_DIR, backend=os.getenv("EVIDENCE_BACKEND", "pgvector"), index=False
        )
        reader: CaseWorkflow | None = None

        async def case_context(case_id: str):  # bound once the checkpoints are open, below
            return await reader.case_context(case_id)

        services = WorkflowServices(
            master_data=PostgresMasterDataService(database),
            intelligence=ConnectionPerCallIntelligence(
                database,
                graph,
                ExternalRiskService(McpRiskClient(os.environ["EXTERNAL_RISK_MCP_URL"])),
                costs,
            ),
            evidence=evidence,
            planning=DeterministicRecoveryPlanningService.from_policy_file(POLICIES_FILE, costs),
            approval=DeterministicApprovalService(
                PostgresApprovalRecords(database),
                load_approval_policy(POLICIES_FILE),
                case_context=case_context,
                costs=costs,
                transfer_cost_eur_per_unit=Decimal(str(policies()["transfer_cost_eur_per_unit"])),
            ),
            execution=McpProcurementClient(os.environ["PROCUREMENT_MCP_URL"]),
            memory=EvidenceResolutionMemoryStore(evidence),
            content_safety=GuardrailContentSafetyService(),
        )
        saver = await stack.enter_async_context(postgres_checkpointer(database_url))
        parts = WorkflowParts(model=model or make_model(), services=services, checkpointer=saver)
        reader = parts.new_workflow()  # any workflow on the shared checkpoints reads every case
        yield parts
