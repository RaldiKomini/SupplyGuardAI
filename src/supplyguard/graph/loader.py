"""Idempotent graph projection from PostgreSQL-owned facts."""

from collections.abc import Iterable, Mapping
from typing import Any

from neo4j import AsyncDriver

NODE_QUERIES = {
    "suppliers": """
        UNWIND $rows AS row
        MERGE (node:Supplier {id: row.supplier_id})
        SET node.name = row.name, node.sourceId = $source_id,
            node.effectiveDate = row.effective_date
    """,
    "upstream_manufacturers": """
        UNWIND $rows AS row
        MERGE (node:UpstreamManufacturer {id: row.upstream_id})
        SET node.name = row.name, node.country = row.country,
            node.sourceId = $source_id, node.effectiveDate = row.effective_date
    """,
    "components": """
        UNWIND $rows AS row
        MERGE (node:Component {id: row.component_id})
        SET node.description = row.description, node.sourceId = $source_id,
            node.effectiveDate = row.effective_date
    """,
    "products": """
        UNWIND $rows AS row
        MERGE (node:Product {id: row.product_id})
        SET node.name = row.name, node.sourceId = $source_id,
            node.effectiveDate = row.effective_date
    """,
    "factories": """
        UNWIND $rows AS row
        MERGE (node:Factory {id: row.factory_id})
        SET node.name = row.name, node.country = row.country,
            node.sourceId = $source_id, node.effectiveDate = row.effective_date
    """,
    "customers": """
        UNWIND $rows AS row
        MERGE (node:Customer {id: row.customer_id})
        SET node.name = row.name, node.sourceId = $source_id,
            node.effectiveDate = row.effective_date
    """,
}

RELATIONSHIP_QUERIES = {
    "supplier_components": """
        UNWIND $rows AS row
        MATCH (a:Supplier {id: row.supplier_id}), (b:Component {id: row.component_id})
        MERGE (a)-[rel:SUPPLIES]->(b)
        SET rel.sourceId = $source_id, rel.effectiveDate = row.effective_date
    """,
    "supplier_upstream": """
        UNWIND $rows AS row
        MATCH (a:Supplier {id: row.supplier_id}),
              (b:UpstreamManufacturer {id: row.upstream_manufacturer_id})
        MERGE (a)-[rel:SOURCES_FROM]->(b)
        SET rel.sourceId = $source_id, rel.effectiveDate = row.effective_date
    """,
    "bill_of_materials": """
        UNWIND $rows AS row
        MATCH (a:Component {id: row.component_id}), (b:Product {id: row.product_id})
        MERGE (a)-[rel:USED_IN]->(b)
        SET rel.quantityPerUnit = row.qty_per_unit,
            rel.sourceId = $source_id, rel.effectiveDate = row.effective_date
    """,
    "product_factories": """
        UNWIND $rows AS row
        MATCH (a:Product {id: row.product_id}), (b:Factory {id: row.factory_id})
        MERGE (a)-[rel:PRODUCED_AT]->(b)
        SET rel.sourceId = $source_id, rel.effectiveDate = row.effective_date
    """,
    "customer_products": """
        UNWIND $rows AS row
        MATCH (a:Product {id: row.product_id}), (b:Customer {id: row.customer_id})
        MERGE (a)-[rel:ORDERED_BY]->(b)
        SET rel.sourceId = $source_id, rel.effectiveDate = row.effective_date
    """,
}


class GraphLoader:
    """Executes only named query templates and uses MERGE for safe re-runs."""

    def __init__(self, driver: AsyncDriver) -> None:
        self.driver = driver

    async def load(
        self,
        dataset: str,
        rows: Iterable[Mapping[str, Any]],
        *,
        source_id: str,
    ) -> int:
        query = NODE_QUERIES.get(dataset) or RELATIONSHIP_QUERIES.get(dataset)
        if query is None:
            raise ValueError(f"unsupported graph dataset: {dataset}")
        materialized = [dict(row) for row in rows]
        if not materialized:
            return 0
        async with self.driver.session() as session:
            result = await session.run(query, rows=materialized, source_id=source_id)
            await result.consume()
        return len(materialized)
