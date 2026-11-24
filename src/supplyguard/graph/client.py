"""Read-only dependency analysis using reviewed, parameterized Cypher."""

from dataclasses import dataclass

from neo4j import AsyncDriver, AsyncGraphDatabase


@dataclass(frozen=True)
class DependencyContext:
    product_ids: tuple[str, ...]
    factory_ids: tuple[str, ...]
    customer_ids: tuple[str, ...]
    upstream_manufacturer_ids: tuple[str, ...]
    upstream_regions: tuple[str, ...]
    alternative_supplier_ids: tuple[str, ...]
    disconnected_entity_ids: tuple[str, ...]


IMPACT_PATH_QUERY = """
MATCH (component:Component {id: $component_id})
OPTIONAL MATCH (component)-[:USED_IN]->(product:Product)
OPTIONAL MATCH (product)-[:PRODUCED_AT]->(factory:Factory)
OPTIONAL MATCH (product)-[:ORDERED_BY]->(customer:Customer)
RETURN collect(DISTINCT product.id) AS product_ids,
       collect(DISTINCT factory.id) AS factory_ids,
       collect(DISTINCT customer.id) AS customer_ids
"""

SUPPLIER_CONTEXT_QUERY = """
MATCH (supplier:Supplier {id: $supplier_id})
OPTIONAL MATCH (supplier)-[:SOURCES_FROM]->(upstream:UpstreamManufacturer)
WITH supplier, collect(DISTINCT upstream.id) AS upstream_ids,
     collect(DISTINCT upstream.country) AS upstream_regions
OPTIONAL MATCH (supplier)-[:SUPPLIES]->(component:Component {id: $component_id})
OPTIONAL MATCH (alternative:Supplier)-[:SUPPLIES]->(component)
WHERE alternative.id <> supplier.id
RETURN upstream_ids, upstream_regions,
       collect(DISTINCT alternative.id) AS alternative_supplier_ids
"""

DISCONNECTED_QUERY = """
MATCH (entity)
WHERE (entity:Supplier OR entity:Component OR entity:Product OR entity:Factory OR entity:Customer)
  AND NOT (entity)--()
RETURN collect(entity.id) AS disconnected_entity_ids
"""


class DependencyGraph:
    """Graph facade. It deliberately does not accept arbitrary Cypher."""

    def __init__(self, driver: AsyncDriver) -> None:
        self.driver = driver

    @classmethod
    def connect(cls, uri: str, user: str, password: str) -> "DependencyGraph":
        return cls(AsyncGraphDatabase.driver(uri, auth=(user, password)))

    async def context_for(self, supplier_id: str, component_id: str) -> DependencyContext:
        async with self.driver.session() as session:
            impact = await (
                await session.run(IMPACT_PATH_QUERY, component_id=component_id)
            ).single()
            supplier = await (
                await session.run(
                    SUPPLIER_CONTEXT_QUERY,
                    supplier_id=supplier_id,
                    component_id=component_id,
                )
            ).single()
            disconnected = await (await session.run(DISCONNECTED_QUERY)).single()
        return DependencyContext(
            product_ids=tuple((impact or {}).get("product_ids", ())),
            factory_ids=tuple((impact or {}).get("factory_ids", ())),
            customer_ids=tuple((impact or {}).get("customer_ids", ())),
            upstream_manufacturer_ids=tuple((supplier or {}).get("upstream_ids", ())),
            upstream_regions=tuple((supplier or {}).get("upstream_regions", ())),
            alternative_supplier_ids=tuple((supplier or {}).get("alternative_supplier_ids", ())),
            disconnected_entity_ids=tuple((disconnected or {}).get("disconnected_entity_ids", ())),
        )

    async def close(self) -> None:
        await self.driver.close()
