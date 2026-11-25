import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from supplyguard.app import chainlit_app
from supplyguard.contracts import (
    ApprovalDecision,
    ApprovalRequest,
    AuthorizationContext,
    CaseStatus,
    CaseSummary,
    RecoveryAction,
    RecoveryActionType,
    UIEvent,
    UIEventType,
)

STALE_MESSAGE = "This approval request is no longer current. Review the latest request."


def _request() -> ApprovalRequest:
    action = RecoveryAction(
        action_id="act-1",
        action_type=RecoveryActionType.EXPEDITE_ORDER,
        payload={"order_id": "PO-1"},
        cost_eur=Decimal("1200.00"),
        expected_completion_date=date(2026, 5, 1),
    )
    return ApprovalRequest(
        approval_request_id="req-1",
        case_id="case-1",
        option_id="opt-1",
        payload_version=1,
        payload_hash="hash-1",
        actions=(action,),
        total_cost_eur=Decimal("1200.00"),
        required_approver_roles=("procurement_manager", "finance_controller"),
        evidence_source_ids=("src-1",),
        requested_at=datetime(2026, 4, 1, tzinfo=UTC),
    )


def _authorization(
    user_id: str = "u-pm-01", role: str = "procurement_manager"
) -> AuthorizationContext:
    return AuthorizationContext(user_id=user_id, role=role)


def _button(request_id: str) -> SimpleNamespace:
    return SimpleNamespace(payload={"approval_request_id": request_id})


class _FakeSession:
    def __init__(self, **values: object) -> None:
        self._values = dict(values)

    def get(self, key: str, default: object = None) -> object:
        return self._values.get(key, default)

    def set(self, key: str, value: object) -> None:
        self._values[key] = value


async def _events(*events: UIEvent | None) -> AsyncIterator[UIEvent]:
    for event in events:
        if event is not None:
            yield event


def _approval_event() -> UIEvent:
    return UIEvent(
        case_id="case-1",
        event_type=UIEventType.APPROVAL,
        stage="await_approval",
        message="Approval needed.",
        occurred_at=datetime(2026, 4, 1, tzinfo=UTC),
        payload={"type": "approval", "request": _request().model_dump(mode="json")},
    )


def _case(case_id: str, status: CaseStatus, updated_at: datetime) -> CaseSummary:
    return CaseSummary(
        case_id=case_id,
        user_id="u-pm-01",
        title="Supplier delay",
        status=status,
        stage="await_approval",
        updated_at=updated_at,
    )


class _FakeService:
    def __init__(
        self,
        error: Exception | None = None,
        *,
        repause: UIEvent | None = None,
        cases: tuple[CaseSummary, ...] = (),
        resume: UIEvent | None = None,
        open_requests: tuple[ApprovalRequest, ...] = (),
        decided: bool = False,
    ) -> None:
        self.decisions: list[ApprovalDecision] = []
        self.resumed: list[str] = []
        self._error = error
        self._repause = repause
        self._cases = cases
        self._resume = resume
        self._open_requests = open_requests
        self._decided = decided

    async def submit_approval(
        self, decision: ApprovalDecision, authorization: AuthorizationContext
    ) -> AsyncIterator[UIEvent]:
        if self._error is not None:
            raise self._error
        self.decisions.append(decision)
        return _events(self._repause)

    async def list_cases(self, authorization: AuthorizationContext) -> tuple[CaseSummary, ...]:
        return self._cases

    async def list_open_approvals(
        self, authorization: AuthorizationContext
    ) -> tuple[ApprovalRequest, ...]:
        return self._open_requests

    async def get_approval_request(
        self, approval_request_id: str, authorization: AuthorizationContext
    ) -> ApprovalRequest:
        for request in self._open_requests:
            if request.approval_request_id == approval_request_id:
                return request
        raise LookupError(approval_request_id)

    async def has_decided(
        self, approval_request_id: str, authorization: AuthorizationContext
    ) -> bool:
        return self._decided

    async def resume_case(
        self, case_id: str, authorization: AuthorizationContext
    ) -> AsyncIterator[UIEvent]:
        self.resumed.append(case_id)
        return _events(self._resume)


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[SimpleNamespace]:
    messages: list[SimpleNamespace] = []

    class _FakeMessage:
        def __init__(self, content: str = "", actions: list | None = None) -> None:
            self.content = content
            self.actions = actions or []

        async def send(self) -> None:
            messages.append(SimpleNamespace(content=self.content, actions=self.actions))

    monkeypatch.setattr(chainlit_app.cl, "Message", _FakeMessage)
    return messages


@pytest.fixture
def session(monkeypatch: pytest.MonkeyPatch) -> _FakeSession:
    fake = _FakeSession(
        authorization=_authorization(),
        pending_approval=_request().model_dump(mode="json"),
    )
    monkeypatch.setattr(chainlit_app.cl, "user_session", fake)
    return fake


@pytest.fixture
def service(monkeypatch: pytest.MonkeyPatch) -> _FakeService:
    fake = _FakeService()
    monkeypatch.setattr(chainlit_app, "_service", fake)
    return fake


@pytest.mark.parametrize("approved", [True, False])
def test_decision_names_only_the_signed_in_user_and_role(approved: bool) -> None:
    pending = _request().model_dump(mode="json")

    decision = chainlit_app.build_decision(pending, _authorization(), approved=approved)

    assert decision.approval_request_id == "req-1"
    assert decision.case_id == "case-1"
    assert decision.payload_hash == "hash-1"
    assert decision.approved is approved
    assert decision.approver_user_ids == ("u-pm-01",)
    assert decision.approver_roles == ("procurement_manager",)


def test_approval_summary_lists_total_roles_and_actions() -> None:
    summary = chainlit_app.approval_summary(_request())

    assert "Total cost: €1200.00" in summary
    assert "Required approver roles: procurement_manager, finance_controller" in summary
    assert "- expedite_order: €1200.00, expected 2026-05-01" in summary


def test_approval_summary_omits_a_missing_completion_date() -> None:
    request = _request()
    undated = request.actions[0].model_copy(update={"expected_completion_date": None})

    summary = chainlit_app.approval_summary(request.model_copy(update={"actions": (undated,)}))

    assert "expected" not in summary


def test_approval_pause_keeps_the_request_on_the_server(session, sent) -> None:
    asyncio.run(chainlit_app._ask_for_approval(_approval_event()))

    assert session.get("pending_approval")["approval_request_id"] == "req-1"
    assert [action.name for action in sent[0].actions] == ["approve_request", "reject_request"]
    assert all(action.payload == {"approval_request_id": "req-1"} for action in sent[0].actions)


def test_matching_decision_is_submitted_and_cleared(session, service, sent) -> None:
    asyncio.run(chainlit_app._decide(_button("req-1"), approved=True))

    assert [decision.approver_user_ids for decision in service.decisions] == [("u-pm-01",)]
    assert session.get("pending_approval") is None


def test_button_for_an_old_request_is_refused(session, service, sent) -> None:
    asyncio.run(chainlit_app._decide(_button("req-old"), approved=True))

    assert service.decisions == []
    assert session.get("pending_approval") is not None
    assert sent[0].content == STALE_MESSAGE


def test_refused_decision_keeps_the_request_pending(session, monkeypatch, sent) -> None:
    monkeypatch.setattr(
        chainlit_app, "_service", _FakeService(error=PermissionError("not your approval"))
    )

    asyncio.run(chainlit_app._decide(_button("req-1"), approved=False))

    assert session.get("pending_approval")["approval_request_id"] == "req-1"
    assert "could not be recorded" in sent[0].content


def test_user_without_an_approver_role_gets_no_buttons(session, sent) -> None:
    session.set("authorization", _authorization(user_id="u-an-01", role="operations_analyst"))

    asyncio.run(chainlit_app._ask_for_approval(_approval_event()))

    assert sent[0].actions == []
    assert "Your role cannot approve this request." in sent[0].content
    assert session.get("pending_approval") is None


def test_approval_recorded_by_this_user_hides_the_buttons(session, monkeypatch, sent) -> None:
    fake = _FakeService(repause=_approval_event(), decided=True)
    monkeypatch.setattr(chainlit_app, "_service", fake)

    asyncio.run(chainlit_app._decide(_button("req-1"), approved=True))

    assert sent[-1].actions == []
    assert "Your approval is recorded. Waiting for approval from finance_controller." in (
        sent[-1].content
    )
    assert session.get("pending_approval") is None


def test_reload_does_not_offer_buttons_again_after_this_users_approval(
    session, monkeypatch, sent
) -> None:
    done = _case("case-1", CaseStatus.AWAITING_APPROVAL, datetime(2026, 4, 2, tzinfo=UTC))
    fake = _FakeService(cases=(done,), resume=_approval_event(), decided=True)
    monkeypatch.setattr(chainlit_app, "_service", fake)

    asyncio.run(chainlit_app._restore_waiting_approval(_authorization()))

    assert sent[0].actions == []
    assert session.get("pending_approval") is None


def test_unexpected_failure_restores_the_pending_request(session, monkeypatch, sent) -> None:
    fake = _FakeService(error=RuntimeError("database down"))
    monkeypatch.setattr(chainlit_app, "_service", fake)

    asyncio.run(chainlit_app._decide(_button("req-1"), approved=True))

    assert session.get("pending_approval")["approval_request_id"] == "req-1"
    assert sent[0].content == "The decision could not be recorded. Please try again."


def test_inbox_lists_each_open_approval_with_a_review_action(session, monkeypatch, sent) -> None:
    monkeypatch.setattr(chainlit_app, "_service", _FakeService(open_requests=(_request(),)))

    asyncio.run(chainlit_app.show_approvals(SimpleNamespace(payload={})))

    assert "case case-1" in sent[0].content
    assert [action.name for action in sent[0].actions] == ["review_approval"]
    assert sent[0].actions[0].payload == {"approval_request_id": "req-1"}


def test_inbox_says_so_when_nothing_is_waiting(session, service, sent) -> None:
    asyncio.run(chainlit_app.show_approvals(SimpleNamespace(payload={})))

    assert sent[0].content == "No approvals are waiting for your role."
    assert sent[0].actions == []


def test_review_opens_the_request_with_buttons_for_an_approver(session, monkeypatch, sent) -> None:
    monkeypatch.setattr(chainlit_app, "_service", _FakeService(open_requests=(_request(),)))

    asyncio.run(chainlit_app.review_approval(_button("req-1")))

    assert [action.name for action in sent[0].actions] == ["approve_request", "reject_request"]
    assert session.get("pending_approval")["approval_request_id"] == "req-1"


def test_review_by_a_user_who_already_decided_shows_no_buttons(session, monkeypatch, sent) -> None:
    fake = _FakeService(open_requests=(_request(),), decided=True)
    monkeypatch.setattr(chainlit_app, "_service", fake)

    asyncio.run(chainlit_app.review_approval(_button("req-1")))

    assert sent[0].actions == []
    assert "Your approval is recorded." in sent[0].content


def test_review_by_a_role_that_cannot_approve_shows_no_buttons(session, monkeypatch, sent) -> None:
    session.set("authorization", _authorization(user_id="u-an-01", role="operations_analyst"))
    monkeypatch.setattr(chainlit_app, "_service", _FakeService(open_requests=(_request(),)))

    asyncio.run(chainlit_app.review_approval(_button("req-1")))

    assert sent[0].actions == []
    assert "Your role cannot approve this request." in sent[0].content


def test_review_of_an_unknown_request_is_reported(session, monkeypatch, sent) -> None:
    monkeypatch.setattr(chainlit_app, "_service", _FakeService())

    asyncio.run(chainlit_app.review_approval(_button("req-missing")))

    assert sent[0].actions == []
    assert "That approval could not be opened" in sent[0].content


def test_latest_waiting_approval_is_shown_again_after_reload(session, monkeypatch, sent) -> None:
    older = _case("case-0", CaseStatus.AWAITING_APPROVAL, datetime(2026, 4, 1, tzinfo=UTC))
    newer = _case("case-1", CaseStatus.AWAITING_APPROVAL, datetime(2026, 4, 2, tzinfo=UTC))
    done = _case("case-2", CaseStatus.COMPLETED, datetime(2026, 4, 3, tzinfo=UTC))
    fake = _FakeService(cases=(older, newer, done), resume=_approval_event())
    monkeypatch.setattr(chainlit_app, "_service", fake)

    asyncio.run(chainlit_app._restore_waiting_approval(_authorization()))

    assert fake.resumed == ["case-1"]
    assert [action.name for action in sent[0].actions] == ["approve_request", "reject_request"]
    assert session.get("pending_approval")["approval_request_id"] == "req-1"


def test_reload_without_a_waiting_approval_shows_nothing(session, monkeypatch, sent) -> None:
    done = _case("case-2", CaseStatus.COMPLETED, datetime(2026, 4, 3, tzinfo=UTC))
    fake = _FakeService(cases=(done,))
    monkeypatch.setattr(chainlit_app, "_service", fake)

    asyncio.run(chainlit_app._restore_waiting_approval(_authorization()))

    assert fake.resumed == []
    assert sent == []
