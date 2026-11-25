"""PostgreSQL checkpoints: every finished workflow step is saved, so cases survive a restart.

Windows: psycopg's async mode cannot run on Windows' default ProactorEventLoop. Call
use_windows_compatible_event_loop() once at startup, before any event loop starts (the app
entry point does this), or run with asyncio.run(..., loop_factory=asyncio.SelectorEventLoop).
"""

import asyncio
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

WINDOWS_LOOP_HINT = (
    "PostgreSQL checkpoints need asyncio.SelectorEventLoop on Windows. Call "
    "use_windows_compatible_event_loop() at startup, or run with "
    "asyncio.run(..., loop_factory=asyncio.SelectorEventLoop)."
)


def use_windows_compatible_event_loop() -> None:
    """Make new event loops SelectorEventLoops on Windows; does nothing elsewhere."""
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


@asynccontextmanager
async def postgres_checkpointer(
    database_url: str | None = None,
) -> AsyncIterator[AsyncPostgresSaver]:
    """Open the checkpointer on DATABASE_URL and create its tables on first use."""
    if sys.platform == "win32" and isinstance(
        asyncio.get_running_loop(), asyncio.ProactorEventLoop
    ):
        raise RuntimeError(WINDOWS_LOOP_HINT)
    url = (database_url or os.environ["DATABASE_URL"]).replace(
        "postgresql+asyncpg://", "postgresql://"
    )
    async with AsyncPostgresSaver.from_conn_string(url) as saver:
        await saver.setup()  # creates the checkpoint tables if they do not exist
        yield saver
