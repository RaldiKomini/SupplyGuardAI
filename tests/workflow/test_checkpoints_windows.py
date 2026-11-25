"""The team works on Windows: the Postgres checkpointer must fail clearly on the wrong loop,
and the startup helper must fix it. No database is needed for these checks."""

import asyncio
import sys

import pytest

from supplyguard.workflow.checkpoints import (
    WINDOWS_LOOP_HINT,
    postgres_checkpointer,
    use_windows_compatible_event_loop,
)

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="Windows event loop behaviour")


@windows_only
def test_default_windows_loop_gets_a_clear_error() -> None:
    async def open_it() -> None:
        async with postgres_checkpointer("postgresql://unused@127.0.0.1:1/none"):
            pass

    with pytest.raises(RuntimeError) as error:
        asyncio.run(open_it(), loop_factory=asyncio.ProactorEventLoop)
    assert str(error.value) == WINDOWS_LOOP_HINT


@windows_only
def test_startup_helper_switches_to_selector_loops() -> None:
    previous = asyncio.get_event_loop_policy()
    try:
        use_windows_compatible_event_loop()
        loop = asyncio.new_event_loop()
        assert isinstance(loop, asyncio.SelectorEventLoop)
        loop.close()
    finally:
        asyncio.set_event_loop_policy(previous)
