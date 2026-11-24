"""Command-line entry point for loading supplied structured data."""

import argparse
import asyncio
import os
from pathlib import Path

from supplyguard.data import Database, StructuredDataLoader


async def _load(directory: Path) -> None:
    database = Database(os.environ["DATABASE_URL"])
    try:
        async with database.transaction() as connection:
            loader = StructuredDataLoader(connection)
            await loader.prepare_schemas()
            summary = await loader.load_directory(directory)
        print(
            f"Staged {summary.staged_rows} rows; loaded {summary.core_rows} core rows; "
            f"found {len(summary.warnings)} validation warning(s)."
        )
    finally:
        await database.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Load SupplyGuard structured support data")
    parser.add_argument("directory", type=Path)
    arguments = parser.parse_args()
    asyncio.run(_load(arguments.directory))


if __name__ == "__main__":
    main()
