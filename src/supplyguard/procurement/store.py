"""PostgreSQL execution store on the procurement schema (migration 0002).

The primary key on idempotency_key is what makes a claim safe: only one run
can insert the pending row, so only one run performs the write.
"""

from sqlalchemy import insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from supplyguard.contracts import (
    ApprovalRequest,
    ExecutionResult,
    ExecutionStatus,
    RecoveryAction,
)
from supplyguard.data.database import Database
from supplyguard.procurement.tables import approval_requests, audit_log, executed_actions

ACTOR = "procurement-mcp"
PENDING = "pending"


class PostgresExecutionStore:
    """Keeps execution outcomes in procurement.executed_actions, with an audit trail."""

    def __init__(self, database: Database) -> None:
        self.database = database

    async def get(self, idempotency_key: str) -> ExecutionResult | None:
        query = (
            select(executed_actions, approval_requests.c.case_id)
            .join(
                approval_requests,
                approval_requests.c.approval_request_id == executed_actions.c.approval_request_id,
            )
            .where(executed_actions.c.idempotency_key == idempotency_key)
            .where(executed_actions.c.status != PENDING)
        )
        async with self.database.connection() as connection:
            row = (await connection.execute(query)).mappings().first()
        if row is None:
            return None
        return ExecutionResult(
            case_id=row["case_id"],
            approval_request_id=row["approval_request_id"],
            action_id=row["action_id"],
            action_type=row["action_type"],
            idempotency_key=row["idempotency_key"],
            status=ExecutionStatus(row["status"]),
            message=row["message"] or "",
            executed_at=row["executed_at"],
            external_reference=row["external_ref"],
        )

    async def claim(
        self, idempotency_key: str, request: ApprovalRequest, action: RecoveryAction
    ) -> bool:
        async with self.database.transaction() as connection:
            claimed = await connection.execute(
                pg_insert(executed_actions)
                .values(
                    idempotency_key=idempotency_key,
                    approval_request_id=request.approval_request_id,
                    action_id=action.action_id,
                    action_type=str(action.action_type),
                    payload=action.model_dump(mode="json")["payload"],
                    status=PENDING,
                )
                .on_conflict_do_nothing()
            )
            if claimed.rowcount != 1:
                return False
            await connection.execute(
                _audit(request.case_id, "execution_claimed", {"idempotency_key": idempotency_key})
            )
        return True

    async def complete(self, result: ExecutionResult) -> None:
        async with self.database.transaction() as connection:
            await connection.execute(
                update(executed_actions)
                .where(executed_actions.c.idempotency_key == result.idempotency_key)
                .values(
                    status=str(result.status),
                    external_ref=result.external_reference,
                    message=result.message,
                    executed_at=result.executed_at,
                )
            )
            await connection.execute(
                _audit(
                    result.case_id,
                    f"execution_{result.status}",
                    {
                        "idempotency_key": result.idempotency_key,
                        "external_reference": result.external_reference,
                    },
                )
            )


def _audit(case_id: str, event: str, details: dict[str, object]):
    # Only identifiers go into the audit log: no credentials or document text.
    return insert(audit_log).values(case_id=case_id, actor=ACTOR, event=event, details=details)
