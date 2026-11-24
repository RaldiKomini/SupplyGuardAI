import io
import os
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from testcontainers.community.postgres import PostgresContainer

ROOT = Path(__file__).resolve().parents[2]
PREVIOUS = "0001_operational_data"
REVISION = "0002_procurement_records"


def _config(output: io.StringIO | None = None) -> Config:
    config = Config(str(ROOT / "alembic.ini"), output_buffer=output)
    config.set_main_option("script_location", str(ROOT / "migrations"))
    return config


def test_procurement_migration_compiles_to_postgres_ddl() -> None:
    output = io.StringIO()
    command.upgrade(_config(output), f"{PREVIOUS}:{REVISION}", sql=True)
    ddl = output.getvalue()

    for table in ("approval_requests", "approval_decisions", "executed_actions", "audit_log"):
        assert f"CREATE TABLE procurement.{table}" in ddl
    assert "PRIMARY KEY (idempotency_key)" in ddl
    assert "PRIMARY KEY (approval_request_id, user_id)" in ddl
    assert "UNIQUE (case_id, payload_version)" in ddl


@pytest.mark.integration
@pytest.mark.skipif(os.getenv("RUN_INTEGRATION") != "1", reason="set RUN_INTEGRATION=1")
def test_postgres_enforces_unique_execution_key(monkeypatch: pytest.MonkeyPatch) -> None:
    with PostgresContainer("postgres:17-alpine", driver=None) as postgres:
        url = postgres.get_connection_url()
        monkeypatch.setenv("DATABASE_URL", url)
        command.upgrade(_config(), PREVIOUS)
        command.upgrade(_config(), REVISION)

        with psycopg.connect(url, autocommit=True) as connection:
            connection.execute("""
                INSERT INTO procurement.approval_requests
                    (approval_request_id, case_id, option_id, payload_version, payload_hash,
                     payload, total_eur, required_roles, requested_at)
                VALUES ('apr-1', 'case-1', 'option-1', 1, 'sha256:test',
                        '[{"action_id":"transfer-1"}]', 96, ARRAY['procurement_manager'], now())
            """)
            execution = """
                INSERT INTO procurement.executed_actions
                    (idempotency_key, approval_request_id, action_id, action_type, payload, status)
                VALUES (%s, 'apr-1', %s, 'transfer_inventory', '{}', 'pending')
            """
            connection.execute(execution, ("case-1:step-1", "transfer-1"))
            with pytest.raises(psycopg.errors.UniqueViolation) as duplicate:
                connection.execute(execution, ("case-1:step-1", "transfer-2"))
            assert duplicate.value.diag.constraint_name == "executed_actions_pkey"

        command.downgrade(_config(), PREVIOUS)
        with psycopg.connect(url) as connection:
            assert connection.execute("SELECT to_regnamespace('procurement')").fetchone() == (None,)
