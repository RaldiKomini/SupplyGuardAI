"""Tests for the application boundary that enforces case ownership."""

import asyncio
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from supplyguard.app.case_service import CaseNotFoundError, CaseService, RequestRejectedError
from supplyguard.auth import AccessRegistry, load_access_config
from supplyguard.contracts import (
    AuthorizationContext,
    CaseStatus,
    CaseSummary,
    IncidentInput,
    UIEvent,
    UIEventType,
)
from supplyguard.contracts.planning import ApprovalDecision, ApprovalRequest

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "config" / "access.toml"
NOW = datetime(2026, 10, 19, 9, 0, tzinfo=UTC)
PASSWORDS = {user.password_env: f"pw-{user.id}" for user in load_access_config(CONFIG_PATH).users}


class InMemoryWorkflow:
    """Stores cases without ownership checks, except `submit_approval`, which follows the contract.

    The contract lets the case owner or an authorized approver submit a decision, so the
    approver test fails if the boundary forwards a decision the workflow would reject.
    """

    def __init__(
        self,
        cases: dict[str, CaseSummary] | None = None,
        requests: dict[str, ApprovalRequest] | None = None,
    ) -> None:
        self.cases: dict[str, CaseSummary] = cases or {}
        self.requests: dict[str, ApprovalRequest] = requests or {}
        self.calls: list[str] = []

    async def list_cases(self, authorization: AuthorizationContext) -> Sequence[CaseSummary]:
        return tuple(self.cases.values())

    async def start_case(
        self, incident: IncidentInput, authorization: AuthorizationContext
    ) -> AsyncIterator[UIEvent]:
        self.calls.append("start_case")
        self.cases[incident.case_id] = _case(incident.case_id, incident.user_id)
        yield _event(incident.case_id, "started")

    async def send_message(
        self, case_id: str, message: str, authorization: AuthorizationContext
    ) -> AsyncIterator[UIEvent]:
        self.calls.append("send_message")
        yield _event(case_id, message)

    async def resume_case(
        self, case_id: str, authorization: AuthorizationContext
    ) -> AsyncIterator[UIEvent]:
        self.calls.append("resume_case")
        yield _event(case_id, "resumed")

    async def submit_approval(
        self, decision: ApprovalDecision, authorization: AuthorizationContext
    ) -> AsyncIterator[UIEvent]:
        self.calls.append("submit_approval")
        case = self.cases.get(decision.case_id)
        request = self.requests.get(decision.approval_request_id)
        is_owner = case is not None and case.user_id == authorization.user_id
        is_approver = (
            request is not None
            and request.case_id == decision.case_id
            and request.payload_hash == decision.payload_hash
            and authorization.role in request.required_approver_roles
        )
        if not (is_owner or is_approver):
            raise PermissionError(decision.case_id)
        yield _event(decision.case_id, "approval recorded")


def _case(case_id: str, user_id: str) -> CaseSummary:
    return CaseSummary(
        case_id=case_id,
        user_id=user_id,
        title="MC-17 delay",
        status=CaseStatus.IN_PROGRESS,
        stage="impact",
        updated_at=NOW,
    )


def _event(case_id: str, message: str) -> UIEvent:
    return UIEvent(
        case_id=case_id,
        event_type=UIEventType.PROGRESS,
        stage="impact",
        message=message,
        occurred_at=NOW,
    )


def _decision(case_id: str, approver: str) -> ApprovalDecision:
    return ApprovalDecision(
        approval_request_id="AR-1",
        case_id=case_id,
        payload_hash="hash-1",
        approved=True,
        approver_user_ids=(approver,),
        approver_roles=(_context(approver).role,),
        decided_at=NOW,
    )


def _approval_request(case_id: str = "C-1") -> ApprovalRequest:
    return ApprovalRequest(
        approval_request_id="AR-1",
        case_id=case_id,
        option_id="OPT-1",
        payload_version=1,
        payload_hash="hash-1",
        actions=(),
        total_cost_eur=Decimal("30378"),
        required_approver_roles=("procurement_manager", "finance_controller"),
        evidence_source_ids=("DOC-1",),
        requested_at=NOW,
    )


class InMemoryApprovals:
    def __init__(self, *requests: ApprovalRequest) -> None:
        self.requests = {request.approval_request_id: request for request in requests}

    async def get_request(self, approval_request_id: str) -> ApprovalRequest | None:
        return self.requests.get(approval_request_id)

    async def list_open_for(self, role: str, user_id: str) -> tuple[ApprovalRequest, ...]:
        return tuple(r for r in self.requests.values() if role in r.required_approver_roles)

    async def has_decided(self, approval_request_id: str, user_id: str) -> bool:
        return False


def _registry(environ: dict[str, str] | None = None) -> AccessRegistry:
    return AccessRegistry(load_access_config(CONFIG_PATH), environ=environ or PASSWORDS)


def _context(user_id: str) -> AuthorizationContext:
    context = _registry().authorization_for(user_id)
    assert context is not None
    return context


def _collect(stream: AsyncIterator[UIEvent]) -> list[UIEvent]:
    async def run() -> list[UIEvent]:
        return [event async for event in stream]

    return asyncio.run(run())


def _owned_by(case_id: str, user_id: str) -> InMemoryWorkflow:
    return InMemoryWorkflow({case_id: _case(case_id, user_id)})


def _pending_approval_service() -> tuple[InMemoryWorkflow, CaseService]:
    request = _approval_request()
    workflow = InMemoryWorkflow(
        {"C-1": _case("C-1", "u-pm-01")}, {request.approval_request_id: request}
    )
    return workflow, CaseService(workflow, InMemoryApprovals(request))


def test_user_lists_only_own_cases() -> None:
    workflow = InMemoryWorkflow({"C-1": _case("C-1", "u-pm-01"), "C-2": _case("C-2", "u-an-01")})
    service = CaseService(workflow)

    cases = asyncio.run(service.list_cases(_context("u-pm-01")))

    assert [case.case_id for case in cases] == ["C-1"]


def test_owner_can_resume_own_case() -> None:
    workflow = _owned_by("C-1", "u-pm-01")
    service = CaseService(workflow)

    events = _collect(asyncio.run(service.resume_case("C-1", _context("u-pm-01"))))

    assert [event.message for event in events] == ["resumed"]
    assert workflow.calls == ["resume_case"]


def test_other_user_cannot_resume_case_and_workflow_is_not_called() -> None:
    workflow = _owned_by("C-1", "u-pm-01")
    service = CaseService(workflow)

    with pytest.raises(CaseNotFoundError):
        asyncio.run(service.resume_case("C-1", _context("u-an-01")))

    assert workflow.calls == []


def test_foreign_and_missing_cases_produce_the_same_error() -> None:
    service = CaseService(_owned_by("C-1", "u-pm-01"))
    context = _context("u-an-01")

    with pytest.raises(CaseNotFoundError) as foreign:
        asyncio.run(service.resume_case("C-1", context))
    with pytest.raises(CaseNotFoundError) as missing:
        asyncio.run(service.resume_case("C-404", context))

    assert type(foreign.value) is type(missing.value)
    assert foreign.value.args == ("C-1",)
    assert missing.value.args == ("C-404",)


def test_other_user_cannot_send_message_to_case() -> None:
    workflow = _owned_by("C-1", "u-pm-01")
    service = CaseService(workflow)

    with pytest.raises(CaseNotFoundError):
        asyncio.run(service.send_message("C-1", "change the supplier", _context("u-fc-01")))

    assert workflow.calls == []


def test_blank_message_is_rejected_before_reaching_workflow() -> None:
    workflow = _owned_by("C-1", "u-pm-01")
    service = CaseService(workflow)

    with pytest.raises(RequestRejectedError):
        asyncio.run(service.send_message("C-1", "   ", _context("u-pm-01")))

    assert workflow.calls == []


def test_owner_can_submit_approval_naming_themselves() -> None:
    workflow = _owned_by("C-1", "u-pm-01")
    service = CaseService(workflow)

    events = _collect(
        asyncio.run(service.submit_approval(_decision("C-1", "u-pm-01"), _context("u-pm-01")))
    )

    assert [event.message for event in events] == ["approval recorded"]


def test_approval_cannot_be_submitted_for_another_users_case() -> None:
    workflow = _owned_by("C-1", "u-pm-01")
    service = CaseService(workflow)

    with pytest.raises(CaseNotFoundError):
        asyncio.run(service.submit_approval(_decision("C-1", "u-fc-01"), _context("u-fc-01")))

    assert workflow.calls == []


def test_approval_must_name_the_authenticated_user() -> None:
    workflow = _owned_by("C-1", "u-pm-01")
    service = CaseService(workflow)

    with pytest.raises(RequestRejectedError):
        asyncio.run(service.submit_approval(_decision("C-1", "u-fc-01"), _context("u-pm-01")))

    assert workflow.calls == []


def test_second_approver_submits_own_approval_and_unrelated_user_is_denied() -> None:
    workflow, service = _pending_approval_service()

    events = _collect(
        asyncio.run(service.submit_approval(_decision("C-1", "u-fc-01"), _context("u-fc-01")))
    )

    assert [event.message for event in events] == ["approval recorded"]
    with pytest.raises(CaseNotFoundError):
        asyncio.run(service.submit_approval(_decision("C-1", "u-an-01"), _context("u-an-01")))
    assert workflow.calls == ["submit_approval"]


def test_second_approver_reads_the_approval_request_but_not_the_case() -> None:
    _, service = _pending_approval_service()
    approver = _context("u-fc-01")

    request = asyncio.run(service.get_approval_request("AR-1", approver))

    assert request.payload_hash == "hash-1"
    assert asyncio.run(service.list_cases(approver)) == ()
    with pytest.raises(CaseNotFoundError):
        asyncio.run(service.resume_case("C-1", approver))
    with pytest.raises(CaseNotFoundError):
        asyncio.run(service.send_message("C-1", "hello", approver))
    with pytest.raises(CaseNotFoundError):
        asyncio.run(service.get_approval_request("AR-1", _context("u-an-01")))


def test_approval_must_match_the_request_it_answers() -> None:
    workflow, service = _pending_approval_service()
    changed = _decision("C-1", "u-fc-01").model_copy(update={"payload_hash": "hash-2"})

    with pytest.raises(RequestRejectedError):
        asyncio.run(service.submit_approval(changed, _context("u-fc-01")))

    assert workflow.calls == []


def test_approver_cannot_submit_a_decision_naming_another_user_or_role() -> None:
    workflow, service = _pending_approval_service()
    forged = _decision("C-1", "u-pm-01").model_copy(
        update={
            "approver_user_ids": ("u-pm-01", "u-fc-01"),
            "approver_roles": ("procurement_manager", "finance_controller"),
        }
    )

    with pytest.raises(RequestRejectedError):
        asyncio.run(service.submit_approval(forged, _context("u-pm-01")))

    assert workflow.calls == []


def test_start_case_rejects_incident_for_another_user() -> None:
    workflow = InMemoryWorkflow()
    service = CaseService(workflow)
    incident = IncidentInput(case_id="C-9", user_id="u-pm-01", message="MC-17 is late")

    with pytest.raises(RequestRejectedError):
        asyncio.run(service.start_case(incident, _context("u-an-01")))

    assert workflow.calls == []


def test_owner_starts_case_and_sees_it_listed() -> None:
    workflow = InMemoryWorkflow()
    service = CaseService(workflow)
    incident = IncidentInput(case_id="C-9", user_id="u-an-01", message="MC-17 is late")

    events = _collect(asyncio.run(service.start_case(incident, _context("u-an-01"))))
    cases = asyncio.run(service.list_cases(_context("u-an-01")))

    assert [event.message for event in events] == ["started"]
    assert [case.case_id for case in cases] == ["C-9"]


def test_boundary_passes_context_to_workflow_unchanged() -> None:
    received: list[AuthorizationContext] = []

    class RecordingWorkflow(InMemoryWorkflow):
        async def resume_case(
            self, case_id: str, authorization: AuthorizationContext
        ) -> AsyncIterator[UIEvent]:
            received.append(authorization)
            yield _event(case_id, "resumed")

    workflow = RecordingWorkflow(_owned_by("C-1", "u-fc-01").cases)
    service = CaseService(workflow)
    context = _context("u-fc-01")

    _collect(asyncio.run(service.resume_case("C-1", context)))

    assert received == [context]
    assert "finance-confidential" in received[0].allowed_scopes


def test_ownership_survives_restart() -> None:
    store = _owned_by("C-1", "u-pm-01").cases

    before_restart = CaseService(InMemoryWorkflow(store))
    after_restart = CaseService(InMemoryWorkflow(store))
    fresh_registry = _registry()
    owner = fresh_registry.authorization_for("u-pm-01")
    other = fresh_registry.authorization_for("u-an-01")
    assert owner is not None and other is not None

    assert asyncio.run(before_restart.list_cases(owner))[0].case_id == "C-1"
    assert asyncio.run(after_restart.list_cases(owner))[0].case_id == "C-1"
    with pytest.raises(CaseNotFoundError):
        asyncio.run(after_restart.resume_case("C-1", other))
