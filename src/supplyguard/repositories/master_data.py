"""PostgreSQL candidate lookup for the workflow's clarification step."""

import re

from sqlalchemy import func, select

from supplyguard.contracts import ComponentCandidate, SupplierCandidate
from supplyguard.data import Database
from supplyguard.data.schema import components, supplier_components, suppliers

_SUPPLIER_ID = re.compile(r"s[0-9]+", re.IGNORECASE)
_COMPONENT_ID = re.compile(r"\bMC[\s-]?([0-9]+)\b", re.IGNORECASE)
_ARTICLES = {"a", "an", "the"}


class PostgresMasterDataService:
    """Returns candidates in stable order and leaves clarification to the workflow."""

    def __init__(self, database: Database) -> None:
        self.database = database

    async def find_suppliers(self, query: str) -> tuple[SupplierCandidate, ...]:
        query = " ".join(query.lower().split())
        if not query:
            return ()
        name = func.lower(suppliers.c.name)
        match = (
            suppliers.c.supplier_id == query.upper()
            if _SUPPLIER_ID.fullmatch(query)
            else name.startswith(query, autoescape=True)
        )
        statement = (
            select(suppliers.c.supplier_id, suppliers.c.name)
            .where(match)
            .order_by(suppliers.c.supplier_id)
        )
        async with self.database.connection() as connection:
            rows = (await connection.execute(statement)).all()
        return tuple(SupplierCandidate(supplier_id=row.supplier_id, name=row.name) for row in rows)

    async def find_components(self, query: str) -> tuple[ComponentCandidate, ...]:
        ids = {f"MC-{m.lstrip('0') or '0'}" for m in _COMPONENT_ID.findall(query)}
        if len(ids) > 1:
            return ()  # A single-component incident needs an unambiguous ID.
        statement = select(components.c.component_id, components.c.description)
        if ids:
            statement = statement.where(components.c.component_id.in_(ids))
        else:
            terms = [term for term in query.lower().split() if term not in _ARTICLES]
            if not terms:
                return ()
            description = func.lower(components.c.description)
            statement = statement.where(
                *(description.contains(term, autoescape=True) for term in terms)
            )
        async with self.database.connection() as connection:
            rows = (await connection.execute(statement.order_by(components.c.component_id))).all()
        return tuple(
            ComponentCandidate(component_id=row.component_id, description=row.description)
            for row in rows
        )

    async def suppliers_of(self, component_id: str) -> tuple[SupplierCandidate, ...]:
        statement = (
            select(suppliers.c.supplier_id, suppliers.c.name)
            .join(supplier_components, suppliers.c.supplier_id == supplier_components.c.supplier_id)
            .where(supplier_components.c.component_id == component_id.strip().upper())
            .order_by(suppliers.c.supplier_id)
        )
        async with self.database.connection() as connection:
            rows = (await connection.execute(statement)).all()
        return tuple(SupplierCandidate(supplier_id=row.supplier_id, name=row.name) for row in rows)
