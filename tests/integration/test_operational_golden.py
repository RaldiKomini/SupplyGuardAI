"""Exercise the MC-17 pipeline with the supplied data and real service boundaries."""

import asyncio
import json
import os
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
import uvicorn
from alembic import command
from alembic.config import Config
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from testcontainers.community.neo4j import Neo4jContainer
from testcontainers.community.postgres import PostgresContainer

from supplyguard.contracts import ConfirmedIncident
from supplyguard.contracts.protocols import MasterDataService
from supplyguard.data import Database, StructuredDataLoader
from supplyguard.external_risk.mcp_client import McpRiskClient
from supplyguard.external_risk.mcp_server import server
from supplyguard.external_risk.service import ExternalRiskService
from supplyguard.graph.cli import _load as load_graph
from supplyguard.graph.client import DependencyGraph
from supplyguard.impact import DefaultOperationalIntelligenceService
from supplyguard.repositories import OperationalRepository, PostgresMasterDataService

ROOT = Path(__file__).resolve().parents[2]
SUPPORT = ROOT / "support-data"
STRUCTURED = SUPPORT / "structured"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_INTEGRATION") != "1", reason="set RUN_INTEGRATION=1"),
]


@asynccontextmanager
async def _serve(app) -> AsyncIterator[str]:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        runner = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
        task = asyncio.create_task(runner.serve(sockets=[listener]))
        try:
            async with asyncio.timeout(10):
                while not runner.started:
                    if task.done():
                        await task
                        raise RuntimeError("Test HTTP service exited before startup")
                    await asyncio.sleep(0.01)
            yield f"http://127.0.0.1:{port}"
        finally:
            runner.should_exit = True
            await task


def test_mc17_pipeline_on_postgres_neo4j_and_mcp(monkeypatch: pytest.MonkeyPatch) -> None:
    password = "integration-password"
    with (
        PostgresContainer("pgvector/pgvector:pg17", driver=None) as postgres,
        Neo4jContainer("neo4j:5", password=password) as neo4j,
    ):
        database_url = postgres.get_connection_url()
        graph_url = neo4j.get_connection_url()
        monkeypatch.setenv("DATABASE_URL", database_url)
        monkeypatch.setenv("NEO4J_URI", graph_url)
        monkeypatch.setenv("NEO4J_USER", neo4j.username)
        monkeypatch.setenv("NEO4J_PASSWORD", password)
        config = Config(str(ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(ROOT / "migrations"))
        command.upgrade(config, "head")
        asyncio.run(
            _verify_pipeline(database_url, graph_url, neo4j.username, password, monkeypatch)
        )


async def _verify_pipeline(database_url, graph_url, username, password, monkeypatch) -> None:
    database = Database(database_url)
    graph = DependencyGraph.connect(graph_url, username, password)
    try:
        async with database.transaction() as connection:
            await StructuredDataLoader(connection).load_directory(STRUCTURED)
        await load_graph(STRUCTURED)
        await _verify_master_data(PostgresMasterDataService(database))
        result = await _investigate(database, graph, monkeypatch)
    finally:
        await graph.close()
        await database.close()

    impact = result.impact
    assert impact.total_usable_units == 3200
    assert impact.daily_demand_units == Decimal("450")
    assert impact.projected_shortfall_units == 5350
    assert {row.location_id: row.usable for row in impact.inventory} == {
        "W-STU": 1300,
        "W-KAT": 600,
        "W-LYO": 500,
        "W-HUB": 800,
    }
    assert {row.location_id: row.days_of_cover for row in impact.coverage if row.factory_id} == {
        "W-STU": Decimal("8.7"),
        "W-KAT": Decimal("4.0"),
        "W-LYO": Decimal("3.3"),
    }
    assert set(impact.affected_product_ids) == {"P012", "P023", "P031", "P047", "P058", "P066"}
    assert set(impact.affected_factory_ids) == {"F1", "F2", "F3"}
    assert len(impact.at_risk_orders) == 77
    assert {po.po_id for po in impact.open_purchase_orders} == {"PO-2026-9001", "PO-2026-9002"}
    expedite = next(c for c in impact.calculations if c.calculation == "expedite_cost")
    assert expedite.result == Decimal("882.00")
    assert expedite.sources[0].record_id == "PO-2026-9002"
    shared = next(f for f in result.dependencies.findings if f.finding_type == "shared_upstream")
    assert shared.node_ids == ("S005",)
    assert {source.source_id for source in shared.sources} == {
        "suppliers.csv",
        "supplier_components.csv",
    }
    assert "graph_relational_mismatch" not in {w.issue_type for w in result.warnings}
    assert any(r.region == "TW" and r.severity == "high" for r in result.external_risks)


async def _verify_master_data(lookup: MasterDataService) -> None:
    assert [s.supplier_id for s in await lookup.find_suppliers("  aTLaS ")] == ["S001", "S006"]
    assert [s.supplier_id for s in await lookup.find_suppliers("s004")] == ["S004"]
    assert await lookup.find_suppliers("%") == ()
    assert await lookup.find_components("  ") == ()
    assert [c.component_id for c in await lookup.find_components("component MC 017")] == ["MC-17"]
    assert "MC-17" in {c.component_id for c in await lookup.find_components("the motor board")}
    assert await lookup.find_components("MC-17 or MC-18") == ()
    assert {s.supplier_id for s in await lookup.suppliers_of("MC-17")} == {
        "S001",
        "S002",
        "S003",
        "S004",
        "S005",
    }


async def _investigate(database: Database, graph: DependencyGraph, monkeypatch):
    feed = json.loads((SUPPORT / "external_service" / "risk_feed.json").read_text(encoding="utf-8"))

    async def alerts(request):
        region = request.query_params.get("region")
        return JSONResponse([alert for alert in feed["alerts"] if alert["region"] == region])

    provider = Starlette(routes=[Route("/alerts", alerts)])
    async with _serve(provider) as provider_url:
        monkeypatch.setenv("EXTERNAL_RISK_FEED_URL", provider_url)
        async with _serve(server.streamable_http_app()) as mcp_url, database.connection() as conn:
            service = DefaultOperationalIntelligenceService(
                OperationalRepository(conn),
                graph,
                ExternalRiskService(McpRiskClient(f"{mcp_url}/mcp")),
            )
            return await service.investigate(
                ConfirmedIncident(
                    case_id="GOLD-001",
                    user_id="u-pm-01",
                    supplier_id="S001",
                    component_id="MC-17",
                    delay_days=12,
                    scenario_date=date(2026, 10, 19),
                    spending_cap_eur=Decimal("75000"),
                    original_message="Atlas Components reports a 12-day MC-17 delay.",
                )
            )
