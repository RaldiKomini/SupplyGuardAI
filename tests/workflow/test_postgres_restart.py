"""A case paused for approval survives a real restart: new connection, new graph, same payload."""

import asyncio
import os

import pytest
from langgraph.types import Command

from supplyguard.workflow.checkpoints import postgres_checkpointer
from supplyguard.workflow.graph import build_graph
from supplyguard.workflow.intake.extraction import IncidentExtraction

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_INTEGRATION") != "1", reason="set RUN_INTEGRATION=1"),
]

CONFIG = {"configurable": {"thread_id": "CASE-PG-1"}}
FIRST = {
    "case_id": "CASE-PG-1",
    "user_id": "u-pm-01",
    "authorization": {"user_id": "u-pm-01", "role": "procurement_manager"},
    "messages": [("user", "Atlas Components: 12-day delay on MC-17.")],
}
GOLDEN = IncidentExtraction(
    supplier_query="Atlas Components", component_query="MC-17", delay_days=12
)


def test_paused_case_resumes_after_restart_without_repeating_work(
    scripted_model, make_services
) -> None:
    from testcontainers.community.postgres import PostgresContainer

    services = make_services()
    with PostgresContainer("postgres:17-alpine", driver=None) as postgres:
        url = postgres.get_connection_url()

        async def before_restart() -> dict:
            async with postgres_checkpointer(url) as saver:
                graph = build_graph(
                    model=scripted_model(GOLDEN), services=services, checkpointer=saver
                )
                result = await graph.ainvoke(FIRST, CONFIG)
                return result["__interrupt__"][0].value["request"]

        async def after_restart(request: dict) -> dict:
            async with postgres_checkpointer(url) as saver:  # a fresh connection and graph
                graph = build_graph(model=scripted_model(), services=services, checkpointer=saver)
                state = await graph.aget_state(CONFIG)
                assert state.next == ("await_approval",)
                assert state.values["approval_request"]["payload_hash"] == request["payload_hash"]
                decision = {
                    "approval_request_id": request["approval_request_id"],
                    "case_id": request["case_id"],
                    "payload_hash": request["payload_hash"],
                    "approved": True,
                    "approver_user_ids": ["u-pm-01"],
                    "approver_roles": ["procurement_manager"],
                    "decided_at": "2026-10-19T12:00:00+00:00",
                }
                return await graph.ainvoke(Command(resume=decision), CONFIG)

        # psycopg's async mode can't use Windows' default ProactorEventLoop.
        request = asyncio.run(before_restart(), loop_factory=asyncio.SelectorEventLoop)
        result = asyncio.run(after_restart(request), loop_factory=asyncio.SelectorEventLoop)

    assert result["status"] == "completed"
    assert len(services.execution.writes) == 1
    assert services.intelligence.calls == 1  # the investigation did not run again
