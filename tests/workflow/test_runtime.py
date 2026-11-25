"""open_case_workflow() wires every owner's real component into the workflow.

Connections (PostgreSQL, Neo4j, vector store, checkpoints) are replaced by stand-ins, so the
test checks the wiring itself without infrastructure.
"""

import asyncio
from contextlib import asynccontextmanager

from langgraph.checkpoint.memory import InMemorySaver

import supplyguard.workflow.runtime as runtime
from supplyguard.approval.service import DeterministicApprovalService
from supplyguard.contracts import AuthorizationContext, IncidentInput
from supplyguard.evaluation.cases import EvaluationCategory
from supplyguard.planning.service import DeterministicRecoveryPlanningService
from supplyguard.procurement.mcp_client import McpProcurementClient
from supplyguard.repositories import PostgresMasterDataService
from supplyguard.retrieval.adapters import (
    EvidenceResolutionMemoryStore,
    GuardrailContentSafetyService,
)
from supplyguard.workflow.intake.extraction import IncidentExtraction
from supplyguard.workflow.service import CaseWorkflow

OWNER = AuthorizationContext(user_id="u-pm-01", role="procurement_manager")
GOLDEN = IncidentExtraction(
    supplier_query="Atlas Components", component_query="MC-17", delay_days=12
)


class Closable:
    def __init__(self, *args, **kwargs) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


def test_open_case_workflow_wires_the_real_components(
    monkeypatch, scripted_model, make_services
) -> None:
    for name, value in {
        "DATABASE_URL": "postgresql://unused@127.0.0.1:1/none",
        "NEO4J_URI": "bolt://127.0.0.1:1",
        "NEO4J_USER": "neo4j",
        "NEO4J_PASSWORD": "unused",
        "PROCUREMENT_MCP_URL": "http://127.0.0.1:8100/mcp",
        "EXTERNAL_RISK_MCP_URL": "http://127.0.0.1:8101/mcp",
    }.items():
        monkeypatch.setenv(name, value)
    opened = {}

    def connect(*args):
        opened["graph"] = Closable()
        return opened["graph"]

    @asynccontextmanager
    async def in_memory_checkpointer(url):
        yield InMemorySaver()

    monkeypatch.setattr(runtime, "Database", lambda url: opened.setdefault("db", Closable()))
    monkeypatch.setattr(runtime.DependencyGraph, "connect", staticmethod(connect))
    monkeypatch.setattr(
        runtime.EvidenceService, "from_support_data", classmethod(lambda cls, *a, **k: object())
    )
    monkeypatch.setattr(runtime, "postgres_checkpointer", in_memory_checkpointer)

    async def open_and_inspect():
        async with runtime.open_workflow_parts(model=scripted_model()) as parts:
            services = parts.services
            assert isinstance(parts.new_workflow(), CaseWorkflow)
            assert isinstance(services.master_data, PostgresMasterDataService)
            assert isinstance(services.intelligence, runtime.ConnectionPerCallIntelligence)
            assert isinstance(services.planning, DeterministicRecoveryPlanningService)
            assert isinstance(services.approval, DeterministicApprovalService)
            assert isinstance(services.execution, McpProcurementClient)
            assert isinstance(services.memory, EvidenceResolutionMemoryStore)
            assert isinstance(services.content_safety, GuardrailContentSafetyService)
            assert set(parts.evaluators(OWNER, ())) == {
                EvaluationCategory.AMBIGUITY,
                EvaluationCategory.FOLLOW_UP,
                EvaluationCategory.RESTART_RECOVERY,
            }

            # A case saved on the shared checkpoints (here with stand-in services, since the
            # real ones need databases) is read back by the real approval service.
            saved = CaseWorkflow(
                model=scripted_model(GOLDEN),
                services=make_services(),
                checkpointer=parts.checkpointer,
            )
            incident = IncidentInput(
                case_id="CASE-RT",
                user_id="u-pm-01",
                message="Atlas Components: 12-day delay on MC-17.",
            )
            [_ async for _ in saved.start_case(incident, OWNER)]
            context_incident, impact = await services.approval.case_context("CASE-RT")
            assert context_incident.supplier_id == "S001"
            assert impact.projected_shortfall_units == 5350

        async with runtime.open_case_workflow(model=scripted_model()) as workflow:
            assert isinstance(workflow, CaseWorkflow)  # what the application uses

    asyncio.run(open_and_inspect())
    assert opened["db"].closed and opened["graph"].closed  # closed again on exit
