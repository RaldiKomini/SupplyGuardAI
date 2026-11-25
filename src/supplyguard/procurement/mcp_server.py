"""MCP server for the simulated procurement ERP.

Read tools return case data. There is one write tool, and it takes only an approval
request id: the request and the approvers' decisions are loaded from the approval
tables, never accepted from the caller. It checks the approval again and never runs
the same action twice. There is no tool that creates an order directly.
"""

import os
from dataclasses import asdict
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from mcp.server.fastmcp import FastMCP

from supplyguard.data.database import Database
from supplyguard.procurement.approvals import ApprovalRecords, PostgresApprovalRecords
from supplyguard.procurement.execution import ExecutionStore, execute_approved
from supplyguard.procurement.store import PostgresExecutionStore
from supplyguard.repositories.operational import OperationalRepository

server = FastMCP(
    name="supplyguard-procurement",
    instructions=(
        "Simulated procurement ERP. Read tools return operational data. "
        "execute_approved_request runs a stored approval request only if the stored "
        "decisions approve it, once per action."
    ),
    host=os.getenv("PROCUREMENT_MCP_HOST", "127.0.0.1"),
    port=int(os.getenv("PROCUREMENT_MCP_PORT", "8100")),
)

_database: Database | None = None


def _db() -> Database:
    global _database
    if _database is None:
        _database = Database(os.environ["DATABASE_URL"])
    return _database


def _store() -> ExecutionStore:
    return PostgresExecutionStore(_db())


def _approvals() -> ApprovalRecords:
    return PostgresApprovalRecords(_db())


def _now() -> datetime:
    return datetime.now(UTC)


@server.tool(structured_output=True)
async def get_inventory(component_id: str) -> dict[str, list[dict[str, Any]]]:
    """Stock of a component per location (on hand, reserved, quarantined, usable)."""
    async with _db().connection() as connection:
        rows = await OperationalRepository(connection).inventory_for_component(component_id)
    return {"inventory": [_jsonable(row) for row in rows]}


@server.tool(structured_output=True)
async def get_supplier_options(component_id: str) -> dict[str, list[dict[str, Any]]]:
    """Suppliers of a component with lead time, capacity, MOQ, price and certification."""
    async with _db().connection() as connection:
        rows = await OperationalRepository(connection).supplier_capabilities(component_id)
    return {"suppliers": [_jsonable(row) for row in rows]}


@server.tool(structured_output=True)
async def get_open_purchase_orders(component_id: str) -> dict[str, list[dict[str, Any]]]:
    """Open purchase orders of a component, with expedite terms."""
    async with _db().connection() as connection:
        rows = await OperationalRepository(connection).open_purchase_orders(component_id)
    return {"purchase_orders": [_jsonable(row) for row in rows]}


@server.tool(structured_output=True)
async def get_execution_result(idempotency_key: str) -> dict[str, Any]:
    """The stored outcome of one action, or found=false if it has not run."""
    result = await _store().get(idempotency_key)
    if result is None:
        return {"found": False}
    return {"found": True, "result": result.model_dump(mode="json")}


@server.tool(structured_output=True)
async def execute_approved_request(approval_request_id: str) -> dict[str, list[dict[str, Any]]]:
    """Execute a stored approval request, once per action, if its stored decisions approve it."""
    records = await _approvals().load(approval_request_id)
    if records is None:
        raise ValueError(f"unknown approval request {approval_request_id}")
    request, decision = records
    results = await execute_approved(request, decision, _store(), now=_now())
    return {"results": [result.model_dump(mode="json") for result in results]}


def _jsonable(row: object) -> dict[str, Any]:
    return {
        key: str(value) if isinstance(value, Decimal | date) else value
        for key, value in asdict(row).items()
    }


def main() -> None:
    server.run(transport="streamable-http")


if __name__ == "__main__":
    main()
