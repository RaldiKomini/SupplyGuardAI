"""Each service call of the recovery flow is sent to LangSmith as its own run."""

from unittest.mock import MagicMock

import pytest
from langsmith import Client
from langsmith.run_helpers import tracing_context

from .test_service import _plan_and_request, _vote, setup  # noqa: F401

pytestmark = pytest.mark.asyncio


def _run_names(client: MagicMock) -> list[str]:
    return [call.kwargs["name"] for call in client.create_run.call_args_list]


async def test_every_service_call_is_a_separate_run(setup) -> None:  # noqa: F811
    client = MagicMock(spec=Client)

    with tracing_context(enabled=True, client=client, project_name="supplyguard-test"):
        _, request = await _plan_and_request(setup)
        await setup.approvals.record_decision(
            request, _vote(request, "u-pm-01", "procurement_manager")
        )
        decision = await setup.approvals.record_decision(
            request, _vote(request, "u-fc-01", "finance_controller")
        )
        await setup.procurement.execute(request, decision)

    names = _run_names(client)
    assert names.count("recovery_planning.propose") == 1
    assert names.count("approval.create_request") == 1
    assert names.count("approval.record_decision") == 2
    assert names.count("procurement.execute") == 1
    assert names.count("procurement.execute_approved") == 1


async def test_runs_carry_the_component_and_no_service_object(setup) -> None:  # noqa: F811
    client = MagicMock(spec=Client)

    with tracing_context(enabled=True, client=client, project_name="supplyguard-test"):
        await _plan_and_request(setup)

    first = client.create_run.call_args_list[0].kwargs
    assert first["extra"]["metadata"]["component"] == "planning"
    assert "self" not in first["inputs"]


async def test_nothing_is_sent_when_tracing_is_off(setup) -> None:  # noqa: F811
    client = MagicMock(spec=Client)

    with tracing_context(enabled=False, client=client):
        await _plan_and_request(setup)

    client.create_run.assert_not_called()
