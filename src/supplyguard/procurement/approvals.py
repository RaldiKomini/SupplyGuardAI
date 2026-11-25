"""Approval records: the requests and the decisions that authenticated approvers stored.

Execution never trusts a decision sent by the caller. It loads the request and the
stored decisions by id, and the approval rules are checked against those records.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from sqlalchemy import any_, func, insert, literal, select
from sqlalchemy.exc import IntegrityError

from supplyguard.contracts import ApprovalDecision, ApprovalRequest, RecoveryAction
from supplyguard.data.database import Database
from supplyguard.procurement.tables import approval_decisions, approval_requests

APPROVE = "approve"


@dataclass(frozen=True)
class DecisionRecord:
    """One authenticated user's stored decision on one approval request."""

    user_id: str
    role: str
    decision: str  # approve, reject or modify
    decided_at: datetime


class ApprovalRecords(Protocol):
    async def load(
        self, approval_request_id: str
    ) -> tuple[ApprovalRequest, ApprovalDecision] | None: ...

    async def latest_version(self, case_id: str) -> int: ...


class ApprovalRecordStore(ApprovalRecords, Protocol):
    """Approval records that can also be written (used by the approval service)."""

    async def save_request(self, request: ApprovalRequest) -> None: ...

    async def save_decision(self, approval_request_id: str, record: DecisionRecord) -> None: ...

    async def save_revision(
        self, approval_request_id: str, record: DecisionRecord, revision: ApprovalRequest
    ) -> ApprovalRequest: ...


def decision_from_records(
    request: ApprovalRequest, records: Sequence[DecisionRecord]
) -> ApprovalDecision:
    """Combine the stored decisions into one.

    Approved only when nobody rejected or modified the request and the approving users
    together hold every required role. One approval of a two-role tier is not enough.
    """
    approvals = [r for r in records if r.decision == APPROVE]
    roles_covered = set(request.required_approver_roles) <= {r.role for r in approvals}
    approved = roles_covered and len(approvals) == len(records)
    decided_at = max((r.decided_at for r in records), default=request.requested_at)
    return ApprovalDecision(
        approval_request_id=request.approval_request_id,
        case_id=request.case_id,
        payload_hash=request.payload_hash,
        approved=approved,
        approver_user_ids=tuple(r.user_id for r in approvals),
        approver_roles=tuple(r.role for r in approvals),
        decided_at=decided_at,
        reason=None if records else "no stored decision",
    )


def is_open_for(
    request: ApprovalRequest,
    records: Sequence[DecisionRecord],
    latest_version: int,
    user_id: str,
) -> bool:
    """Open means still waiting: latest version, no rejection or edit, not fully approved,
    and this user has not decided. Both stores use this rule so they cannot disagree."""
    if request.payload_version != latest_version:
        return False
    if any(r.decision != APPROVE for r in records):
        return False
    if any(r.user_id == user_id for r in records):
        return False
    return not decision_from_records(request, records).approved


class InMemoryApprovalRecords:
    """Approval records for tests and the offline demo."""

    def __init__(self) -> None:
        self.requests: dict[str, ApprovalRequest] = {}
        self.decisions: dict[str, list[DecisionRecord]] = {}

    async def save_request(self, request: ApprovalRequest) -> None:
        self.requests[request.approval_request_id] = request

    async def save_decision(self, approval_request_id: str, record: DecisionRecord) -> None:
        records = self.decisions.setdefault(approval_request_id, [])
        if any(existing.user_id == record.user_id for existing in records):
            # Same rule as the primary key in procurement.approval_decisions.
            raise ValueError(f"{record.user_id} has already decided on {approval_request_id}")
        records.append(record)

    async def save_revision(
        self, approval_request_id: str, record: DecisionRecord, revision: ApprovalRequest
    ) -> ApprovalRequest:
        previous = self.requests.get(revision.approval_request_id)
        decisions = self.decisions.get(approval_request_id, [])
        if (
            previous is not None
            and previous.payload_hash == revision.payload_hash
            and record in decisions
            and await self.latest_version(revision.case_id) == revision.payload_version
        ):
            return previous
        if previous is not None:
            raise ValueError("a newer approval request already exists for this case")
        await self.save_decision(approval_request_id, record)
        self.requests[revision.approval_request_id] = revision
        return revision

    async def latest_version(self, case_id: str) -> int:
        versions = [r.payload_version for r in self.requests.values() if r.case_id == case_id]
        return max(versions, default=0)

    async def list_open_for(self, role: str, user_id: str) -> tuple[ApprovalRequest, ...]:
        open_requests = []
        for approval_request_id, request in self.requests.items():
            if role not in request.required_approver_roles:
                continue
            records = self.decisions.get(approval_request_id, [])
            latest = await self.latest_version(request.case_id)
            if is_open_for(request, records, latest, user_id):
                open_requests.append(request)
        return tuple(open_requests)

    async def has_decided(self, approval_request_id: str, user_id: str) -> bool:
        return any(r.user_id == user_id for r in self.decisions.get(approval_request_id, []))

    async def get_request(self, approval_request_id: str) -> ApprovalRequest | None:
        return self.requests.get(approval_request_id)

    async def load(
        self, approval_request_id: str
    ) -> tuple[ApprovalRequest, ApprovalDecision] | None:
        request = self.requests.get(approval_request_id)
        if request is None:
            return None
        records = self.decisions.get(approval_request_id, [])
        return request, decision_from_records(request, records)


class PostgresApprovalRecords:
    """Approval records in procurement.approval_requests and procurement.approval_decisions."""

    def __init__(self, database: Database) -> None:
        self.database = database

    async def save_request(self, request: ApprovalRequest) -> None:
        async with self.database.transaction() as connection:
            await connection.execute(insert(approval_requests).values(request_row(request)))

    async def save_decision(
        self, approval_request_id: str, record: DecisionRecord, comment: str | None = None
    ) -> None:
        try:
            await self._insert_decision(approval_request_id, record, comment)
        except IntegrityError as error:
            raise ValueError(
                f"{record.user_id} has already decided on {approval_request_id}"
            ) from error

    async def _insert_decision(
        self, approval_request_id: str, record: DecisionRecord, comment: str | None
    ) -> None:
        async with self.database.transaction() as connection:
            await connection.execute(_decision_insert(approval_request_id, record, comment))

    async def save_revision(
        self, approval_request_id: str, record: DecisionRecord, revision: ApprovalRequest
    ) -> ApprovalRequest:
        # A rejected decision or duplicate request must roll back both writes.
        try:
            async with self.database.transaction() as connection:
                await connection.execute(_decision_insert(approval_request_id, record))
                await connection.execute(insert(approval_requests).values(request_row(revision)))
        except IntegrityError as error:
            previous = await self._replayed_revision(approval_request_id, record, revision)
            if previous is not None:
                return previous
            raise ValueError(
                f"{record.user_id} has already decided on {approval_request_id}, "
                "or a newer request already exists"
            ) from error
        return revision

    async def _replayed_revision(
        self, approval_request_id: str, record: DecisionRecord, revision: ApprovalRequest
    ) -> ApprovalRequest | None:
        """Return a committed edit when a workflow retries before its checkpoint was saved."""
        saved = await self.load(revision.approval_request_id)
        if saved is None or saved[0].payload_hash != revision.payload_hash:
            return None
        if await self.latest_version(revision.case_id) != revision.payload_version:
            return None
        async with self.database.connection() as connection:
            decision = await connection.scalar(
                select(approval_decisions.c.decision).where(
                    approval_decisions.c.approval_request_id == approval_request_id,
                    approval_decisions.c.user_id == record.user_id,
                    approval_decisions.c.role == record.role,
                    approval_decisions.c.decided_at == record.decided_at,
                )
            )
        return saved[0] if decision == record.decision else None

    async def latest_version(self, case_id: str) -> int:
        async with self.database.connection() as connection:
            latest = await connection.scalar(
                select(func.max(approval_requests.c.payload_version)).where(
                    approval_requests.c.case_id == case_id
                )
            )
        return latest or 0

    async def get_request(self, approval_request_id: str) -> ApprovalRequest | None:
        saved = await self._load_with_records(approval_request_id)
        return None if saved is None else saved[0]

    async def load(
        self, approval_request_id: str
    ) -> tuple[ApprovalRequest, ApprovalDecision] | None:
        saved = await self._load_with_records(approval_request_id)
        if saved is None:
            return None
        request, records = saved
        return request, decision_from_records(request, records)

    async def _load_with_records(
        self, approval_request_id: str
    ) -> tuple[ApprovalRequest, list[DecisionRecord]] | None:
        async with self.database.connection() as connection:
            row = (
                (
                    await connection.execute(
                        select(approval_requests).where(
                            approval_requests.c.approval_request_id == approval_request_id
                        )
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                return None
            decision_rows = (
                await connection.execute(
                    select(approval_decisions)
                    .where(approval_decisions.c.approval_request_id == approval_request_id)
                    .order_by(approval_decisions.c.user_id)
                )
            ).mappings()
            records = [
                DecisionRecord(d["user_id"], d["role"], d["decision"], d["decided_at"])
                for d in decision_rows
            ]
        request = ApprovalRequest(
            approval_request_id=row["approval_request_id"],
            case_id=row["case_id"],
            option_id=row["option_id"],
            payload_version=row["payload_version"],
            payload_hash=row["payload_hash"],
            actions=tuple(RecoveryAction.model_validate(item) for item in row["payload"]),
            total_cost_eur=row["total_eur"],
            required_approver_roles=tuple(row["required_roles"]),
            evidence_source_ids=tuple(row["evidence_source_ids"]),
            requested_at=row["requested_at"],
        )
        return request, records

    async def list_open_for(self, role: str, user_id: str) -> tuple[ApprovalRequest, ...]:
        async with self.database.connection() as connection:
            candidate_ids = (
                (
                    await connection.execute(
                        select(approval_requests.c.approval_request_id).where(
                            literal(role) == any_(approval_requests.c.required_roles)
                        )
                    )
                )
                .scalars()
                .all()
            )
        open_requests = []
        for approval_request_id in candidate_ids:
            saved = await self._load_with_records(approval_request_id)
            if saved is None:
                continue
            request, records = saved
            latest = await self.latest_version(request.case_id)
            if is_open_for(request, records, latest, user_id):
                open_requests.append(request)
        return tuple(open_requests)

    async def has_decided(self, approval_request_id: str, user_id: str) -> bool:
        async with self.database.connection() as connection:
            found = await connection.scalar(
                select(approval_decisions.c.user_id).where(
                    approval_decisions.c.approval_request_id == approval_request_id,
                    approval_decisions.c.user_id == user_id,
                )
            )
        return found is not None


def _decision_insert(approval_request_id: str, record: DecisionRecord, comment: str | None = None):
    return insert(approval_decisions).values(
        approval_request_id=approval_request_id,
        user_id=record.user_id,
        role=record.role,
        decision=record.decision,
        decided_at=record.decided_at,
        comment=comment,
    )


def request_row(request: ApprovalRequest) -> dict[str, object]:
    return {
        "approval_request_id": request.approval_request_id,
        "case_id": request.case_id,
        "option_id": request.option_id,
        "payload_version": request.payload_version,
        "payload_hash": request.payload_hash,
        "payload": [action.model_dump(mode="json") for action in request.actions],
        "total_eur": request.total_cost_eur,
        "required_roles": list(request.required_approver_roles),
        "evidence_source_ids": list(request.evidence_source_ids),
        "requested_at": request.requested_at,
    }
