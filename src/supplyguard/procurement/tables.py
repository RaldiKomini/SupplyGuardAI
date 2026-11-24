"""Table definitions for the procurement schema created by migration 0002."""

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Column,
    DateTime,
    Integer,
    MetaData,
    Numeric,
    Table,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB

# Separate metadata: the migration owns the DDL; these objects are only used for queries.
metadata = MetaData(schema="procurement")

approval_requests = Table(
    "approval_requests",
    metadata,
    Column("approval_request_id", Text, primary_key=True),
    Column("case_id", Text, nullable=False),
    Column("option_id", Text, nullable=False),
    Column("payload_version", Integer, nullable=False),
    Column("payload_hash", Text, nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("total_eur", Numeric(14, 2), nullable=False),
    Column("required_roles", ARRAY(Text), nullable=False),
    Column("evidence_source_ids", ARRAY(Text), nullable=False),
    Column("requested_at", DateTime(timezone=True), nullable=False),
)

executed_actions = Table(
    "executed_actions",
    metadata,
    Column("idempotency_key", Text, primary_key=True),
    Column("approval_request_id", Text, nullable=False),
    Column("action_id", Text, nullable=False),
    Column("action_type", Text, nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("status", Text, nullable=False),
    Column("external_ref", Text),
    Column("message", Text),
    Column("executed_at", DateTime(timezone=True)),
)

audit_log = Table(
    "audit_log",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("case_id", Text, nullable=False),
    Column("actor", Text, nullable=False),
    Column("event", Text, nullable=False),
    Column("details", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True)),
)
