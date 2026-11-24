"""Build the Neo4j projection from unmodified support-data CSVs."""

import argparse
import asyncio
import csv
import os
from pathlib import Path

from neo4j import AsyncGraphDatabase

from supplyguard.graph.loader import GraphLoader


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


async def _load(directory: Path) -> None:
    driver = AsyncGraphDatabase.driver(
        os.environ["NEO4J_URI"],
        auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"]),
    )
    loader = GraphLoader(driver)
    try:
        for dataset in (
            "suppliers",
            "upstream_manufacturers",
            "components",
            "products",
            "factories",
            "customers",
        ):
            await loader.load(
                dataset, _rows(directory / f"{dataset}.csv"), source_id=f"{dataset}.csv"
            )

        suppliers = _rows(directory / "suppliers.csv")
        await loader.load(
            "supplier_upstream",
            [row for row in suppliers if row.get("upstream_manufacturer_id")],
            source_id="suppliers.csv",
        )
        await loader.load(
            "supplier_components",
            _rows(directory / "supplier_components.csv"),
            source_id="supplier_components.csv",
        )
        await loader.load(
            "bill_of_materials",
            _rows(directory / "bill_of_materials.csv"),
            source_id="bill_of_materials.csv",
        )
        await loader.load(
            "product_factories",
            _rows(directory / "products.csv"),
            source_id="products.csv",
        )
        order_pairs = {
            (row["product_id"], row["customer_id"])
            for row in _rows(directory / "customer_orders.csv")
            if row.get("product_id") and row.get("customer_id")
        }
        await loader.load(
            "customer_products",
            [
                {"product_id": product_id, "customer_id": customer_id}
                for product_id, customer_id in sorted(order_pairs)
            ],
            source_id="customer_orders.csv",
        )
    finally:
        await driver.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Load SupplyGuard's Neo4j dependency projection")
    parser.add_argument("directory", type=Path)
    arguments = parser.parse_args()
    asyncio.run(_load(arguments.directory))


if __name__ == "__main__":
    main()
