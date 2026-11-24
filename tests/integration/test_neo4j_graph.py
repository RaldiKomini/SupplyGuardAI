import asyncio
import os

import pytest
from neo4j import AsyncGraphDatabase
from testcontainers.community.neo4j import Neo4jContainer

from supplyguard.graph import DependencyGraph, GraphLoader

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_INTEGRATION") != "1", reason="set RUN_INTEGRATION=1"),
]


def test_graph_loader_and_fixed_queries_against_real_neo4j() -> None:
    password = "integration-password"
    with Neo4jContainer("neo4j:5.26-community", password=password) as neo4j:
        asyncio.run(_exercise_graph(neo4j.get_connection_url(), neo4j.username, password))


async def _exercise_graph(uri: str, username: str, password: str) -> None:
    driver = AsyncGraphDatabase.driver(uri, auth=(username, password))
    try:
        loader = GraphLoader(driver)
        await loader.load(
            "suppliers",
            ({"supplier_id": "S1", "name": "Atlas", "effective_date": "2025-01-01"},),
            source_id="suppliers.csv",
        )
        await loader.load(
            "components",
            (
                {
                    "component_id": "MC-17",
                    "description": "Controller",
                    "effective_date": "2025-01-01",
                },
            ),
            source_id="components.csv",
        )
        await loader.load(
            "products",
            ({"product_id": "P1", "name": "Drive", "effective_date": "2025-01-01"},),
            source_id="products.csv",
        )
        await loader.load(
            "supplier_components",
            ({"supplier_id": "S1", "component_id": "MC-17", "effective_date": "2025-01-01"},),
            source_id="supplier_components.csv",
        )
        await loader.load(
            "bill_of_materials",
            (
                {
                    "component_id": "MC-17",
                    "product_id": "P1",
                    "qty_per_unit": 1,
                    "effective_date": "2025-01-01",
                },
            ),
            source_id="bill_of_materials.csv",
        )
        context = await DependencyGraph(driver).context_for("S1", "MC-17")
        assert context.product_ids == ("P1",)
    finally:
        await driver.close()
