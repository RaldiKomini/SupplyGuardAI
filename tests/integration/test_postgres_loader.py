import asyncio
import os
from pathlib import Path

import pytest
from sqlalchemy import func, select
from testcontainers.community.postgres import PostgresContainer

from supplyguard.data import Database, StructuredDataLoader
from supplyguard.data.schema import inventory, metadata, raw_records

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_INTEGRATION") != "1", reason="set RUN_INTEGRATION=1"),
]


def test_loader_is_idempotent_against_real_postgres(tmp_path: Path) -> None:
    (tmp_path / "inventory.csv").write_text(
        "component_id,location_id,on_hand,reserved,quarantined,usable,last_counted_date\n"
        "MC-17,L1,10,2,1,7,2025-01-01\n",
        encoding="utf-8",
    )
    with PostgresContainer("postgres:17-alpine", driver=None) as postgres:
        asyncio.run(_load_twice(postgres.get_connection_url(), tmp_path))


async def _load_twice(url: str, directory: Path) -> None:
    database = Database(url)
    try:
        async with database.transaction() as connection:
            loader = StructuredDataLoader(connection)
            await loader.prepare_schemas()
            await connection.run_sync(metadata.create_all)
            await loader.load_directory(directory)
            await loader.load_directory(directory)
            core_count = await connection.scalar(select(func.count()).select_from(inventory))
            raw_count = await connection.scalar(select(func.count()).select_from(raw_records))
        assert core_count == 1
        assert raw_count == 1
    finally:
        await database.close()
