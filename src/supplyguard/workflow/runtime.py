"""Builds the case workflow from the real components (central workflow integration).

    use_windows_compatible_event_loop()      # once at startup, before the event loop
    async with open_case_workflow() as workflow:
        ...                                   # e.g. CaseService(workflow, ...)

Every component comes from its owner's package and is used only through its protocol. The
recipe follows the owners' integration tests. Settings come from the environment
(.env.example): DATABASE_URL, NEO4J_URI/USER/PASSWORD, PROCUREMENT_MCP_URL,
EXTERNAL_RISK_MCP_URL, EVIDENCE_BACKEND (memory, pgvector or chroma; default pgvector), and
the model settings read by make_model().
"""

import os
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from decimal import Decimal

from langchain_core.language_models import BaseChatModel

from supplyguard.approval.policy import load_approval_policy
from supplyguard.approval.service import DeterministicApprovalService
from supplyguard.calculations.service import DeterministicCostService
from supplyguard.contracts import ConfirmedIncident, OperationalIntelligenceResult
from supplyguard.contracts.protocols import ApprovalRequestSource
from supplyguard.data.database import Database
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


@asynccontextmanager
async def open_approval_source() -> AsyncIterator[ApprovalRequestSource]:
    """The approval records CaseService reads, on the same database the workflow writes to."""
    database = Database(os.environ["DATABASE_URL"])
    try:
        yield PostgresApprovalRecords(database)
    finally:
        await database.close()


@asynccontextmanager
async def open_case_workflow(model: BaseChatModel | None = None) -> AsyncIterator[CaseWorkflow]:
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
        workflow: CaseWorkflow | None = None

        async def case_context(case_id: str):  # bound once the workflow exists, below
            return await workflow.case_context(case_id)

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
        workflow = CaseWorkflow(model=model or make_model(), services=services, checkpointer=saver)
        yield workflow
