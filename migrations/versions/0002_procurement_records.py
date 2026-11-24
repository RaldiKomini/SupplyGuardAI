"""Persist approval requests, decisions, execution results, and audit events."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0002_procurement_records"
down_revision = "0001_operational_data"
branch_labels = None
depends_on = None

SCHEMA = "procurement"


def upgrade() -> None:
    op.execute("CREATE SCHEMA procurement")
    op.create_table(
        "approval_requests",
        sa.Column("approval_request_id", sa.Text, primary_key=True),
        sa.Column("case_id", sa.Text, nullable=False),
        sa.Column("option_id", sa.Text, nullable=False),
        sa.Column("payload_version", sa.Integer, nullable=False),
        sa.Column("payload_hash", sa.Text, nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("total_eur", sa.Numeric(14, 2), nullable=False),
        sa.Column("required_roles", sa.ARRAY(sa.Text), nullable=False),
        sa.Column("evidence_source_ids", sa.ARRAY(sa.Text), nullable=False, server_default="{}"),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("case_id", "payload_version", name="uq_approval_case_version"),
        sa.CheckConstraint("payload_version >= 1", name="ck_approval_version"),
        sa.CheckConstraint("total_eur >= 0", name="ck_approval_total"),
        schema=SCHEMA,
    )
    op.create_table(
        "approval_decisions",
        sa.Column(
            "approval_request_id",
            sa.Text,
            sa.ForeignKey("procurement.approval_requests.approval_request_id"),
            primary_key=True,
        ),
        sa.Column("user_id", sa.Text, primary_key=True),
        sa.Column("role", sa.Text, nullable=False),
        sa.Column("decision", sa.Text, nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("comment", sa.Text),
        sa.Column("modified_actions", JSONB),
        sa.CheckConstraint(
            "decision IN ('approve', 'reject', 'modify')", name="ck_approval_decision"
        ),
        schema=SCHEMA,
    )
    op.create_table(
        "executed_actions",
        sa.Column("idempotency_key", sa.Text, primary_key=True),
        sa.Column(
            "approval_request_id",
            sa.Text,
            sa.ForeignKey("procurement.approval_requests.approval_request_id"),
            nullable=False,
        ),
        sa.Column("action_id", sa.Text, nullable=False),
        sa.Column("action_type", sa.Text, nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("external_ref", sa.Text),
        sa.Column("message", sa.Text),
        sa.Column("executed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("approval_request_id", "action_id", name="uq_executed_request_action"),
        sa.CheckConstraint(
            "status IN ('pending', 'succeeded', 'rejected', 'failed')",
            name="ck_execution_status",
        ),
        schema=SCHEMA,
    )
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("case_id", sa.Text, nullable=False),
        sa.Column("actor", sa.Text, nullable=False),
        sa.Column("event", sa.Text, nullable=False),
        sa.Column("details", JSONB, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        schema=SCHEMA,
    )
    op.create_index("ix_audit_case_id", "audit_log", ["case_id", "id"], schema=SCHEMA)


def downgrade() -> None:
    op.drop_table("audit_log", schema=SCHEMA)
    op.drop_table("executed_actions", schema=SCHEMA)
    op.drop_table("approval_decisions", schema=SCHEMA)
    op.drop_table("approval_requests", schema=SCHEMA)
    op.execute("DROP SCHEMA procurement")
