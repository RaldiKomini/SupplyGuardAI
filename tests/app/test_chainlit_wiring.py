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


async def _no_events() -> AsyncIterator[UIEvent]:
    for event in ():
        yield event


class _FakeService:
    def __init__(self, error: Exception | None = None) -> None:
        self.decisions: list[ApprovalDecision] = []
        self._error = error

    async def submit_approval(
        self, decision: ApprovalDecision, authorization: AuthorizationContext
    ) -> AsyncIterator[UIEvent]:
        if self._error is not None:
            raise self._error
        self.decisions.append(decision)
        return _no_events()


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
    event = UIEvent(
        case_id="case-1",
        event_type=UIEventType.APPROVAL,
        stage="await_approval",
        message="Approval needed.",
        occurred_at=datetime(2026, 4, 1, tzinfo=UTC),
        payload={"type": "approval", "request": _request().model_dump(mode="json")},
    )

    asyncio.run(chainlit_app._ask_for_approval(event))

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
