"""Each service call of the recovery flow is sent to LangSmith as its own run.

Only safe fields reach LangSmith: confidential evidence text must never appear in a trace.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from langsmith import Client
from langsmith.run_helpers import tracing_context
from planning.golden import IMPACT, INCIDENT

from supplyguard.contracts import EvidenceBundle, EvidenceItem, SourceReference

from .test_service import _plan_and_request, _vote, setup  # noqa: F401

pytestmark = pytest.mark.asyncio

SECRET = "FINANCE-ONLY: negotiated margin 41.7% with Helios, do not share"


def _client() -> MagicMock:
    return MagicMock(spec=Client)


def _run_names(client: MagicMock) -> list[str]:
    return [call.kwargs["name"] for call in client.create_run.call_args_list]


def _runs(client: MagicMock, name: str) -> list[dict]:
    return [c.kwargs for c in client.create_run.call_args_list if c.kwargs["name"] == name]


def _everything_sent(client: MagicMock) -> str:
    return repr(client.mock_calls)


def _confidential_evidence() -> EvidenceBundle:
    return EvidenceBundle(
        case_id=INCIDENT.case_id,
        query=SECRET,
        items=(
            EvidenceItem(
                evidence_id="EV-FIN-1",
                title=SECRET,
                document_type="finance_memo",
                content=SECRET,
                source=SourceReference(
                    source_id="DOC-FIN-1", source_type="document", access_scope="finance"
                ),
            ),
        ),
    )


def _edit(request):
    edited = tuple(
        a.model_copy(update={"payload": {**a.payload, "qty": 2000}})
        if a.payload.get("supplier_id") == "S004"
        else a
        for a in request.actions
    )
    return _vote(request, "u-pm-01", "procurement_manager").model_copy(
        update={"approved": False, "modified_actions": edited, "reason": SECRET}
    )


async def test_every_service_call_is_a_separate_run(setup) -> None:  # noqa: F811
    client = _client()

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
    client = _client()

    with tracing_context(enabled=True, client=client, project_name="supplyguard-test"):
        await _plan_and_request(setup)

    first = client.create_run.call_args_list[0].kwargs
    assert first["extra"]["metadata"]["component"] == "planning"
    assert "self" not in first["inputs"]


async def test_the_edit_path_the_workflow_uses_is_traced(setup) -> None:  # noqa: F811
    _, request = await _plan_and_request(setup)
    client = _client()

    with tracing_context(enabled=True, client=client, project_name="supplyguard-test"):
        revised = await setup.approvals.record_modification(request, _edit(request))

    assert revised.payload_version == 2
    [run] = _runs(client, "approval.record_modification")
    assert run["extra"]["metadata"]["component"] == "approval"
    assert run["inputs"]["decision"]["is_modification"]
    assert run["inputs"]["request"]["payload_version"] == 1


async def test_confidential_evidence_never_reaches_langsmith(setup) -> None:  # noqa: F811
    client = _client()
    incident = INCIDENT.model_copy(update={"original_message": SECRET})

    with tracing_context(enabled=True, client=client, project_name="supplyguard-test"):
        plan = await setup.planning.propose(
            incident, SimpleNamespace(impact=IMPACT), evidence=_confidential_evidence()
        )
        request = await setup.approvals.create_request(plan)
        await setup.approvals.record_modification(request, _edit(request))

    [run] = _runs(client, "recovery_planning.propose")
    assert run["inputs"]["evidence"]["evidence_ids"] == ["EV-FIN-1"]
    assert client.create_run.called
    assert "41.7" not in _everything_sent(client)
    assert "FINANCE-ONLY" not in _everything_sent(client)


async def test_nothing_is_sent_when_tracing_is_off(setup) -> None:  # noqa: F811
    client = _client()

    with tracing_context(enabled=False, client=client):
        await _plan_and_request(setup)

    client.create_run.assert_not_called()
