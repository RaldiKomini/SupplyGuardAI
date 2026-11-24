"""Create staging and operational core schemas."""

from alembic import op

from supplyguard.data.schema import metadata

revision = "0001_operational_data"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS staging")
    op.execute("CREATE SCHEMA IF NOT EXISTS core")
    metadata.create_all(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    metadata.drop_all(bind=op.get_bind(), checkfirst=True)
    op.execute("DROP SCHEMA IF EXISTS core")
    op.execute("DROP SCHEMA IF EXISTS staging")
