import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from supplyguard.app import chainlit_app
from supplyguard.app.case_service import CaseService
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
from supplyguard.procurement.approvals import DecisionRecord, InMemoryApprovalRecords

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
        self.follow_ups: list[tuple[str, str]] = []
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

    async def send_message(
        self, case_id: str, message: str, authorization: AuthorizationContext
    ) -> AsyncIterator[UIEvent]:
        self.follow_ups.append((case_id, message))
        return _events()


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
    assert "Purchase order: PO-1" in summary


def test_approval_summary_distinguishes_equal_cost_actions() -> None:
    original = _request()
    changed = original.actions[0].model_copy(update={"payload": {"po_id": "PO-2", "qty": 20}})
    summary = chainlit_app.approval_summary(original.model_copy(update={"actions": (changed,)}))

    assert "Purchase order: PO-2" in summary
    assert "Quantity: 20" in summary
    assert summary != chainlit_app.approval_summary(original)


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


def test_owner_sees_conflicts_and_options_before_approval_buttons(session, sent) -> None:
    event = _approval_event()
    context = {
        "evidence": {
            "case_id": event.case_id,
            "query": "supplier delay",
            "items": [],
            "conflicts": [
                {
                    "subject": "S001.delay_days",
                    "description": "Formal notice says 12 days.",
                    "conflicting_evidence_ids": ["email"],
                }
            ],
        },
        "plan": {
            "case_id": event.case_id,
            "options": [],
            "recommended_option_id": None,
            "recommendation_reason": "No feasible option",
        },
    }
    context["delay_conflict_highlight"] = "> **Supplier sources disagree**"
    approval = event.model_copy(update={"payload": {**event.payload, **context}})

    asyncio.run(chainlit_app._show(_events(approval)))

    preview, buttons = sent
    assert (
        "Formal notice says 12 days." in preview.content
        and "## Options compared" in preview.content
    )
    assert buttons.content.startswith("> **Supplier sources disagree**")
    assert buttons.actions[0].name == "approve_request"


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


def test_review_rechecks_a_request_closed_since_listing(session, monkeypatch, sent) -> None:
    request = _request()
    records = InMemoryApprovalRecords()
    service = CaseService(SimpleNamespace(), records)
    finance = _authorization("u-fc-01", "finance_controller")
    session.set("authorization", finance)
    session.set("pending_approval", None)
    monkeypatch.setattr(chainlit_app, "_service", service)

    async def scenario() -> None:
        await records.save_request(request)
        await chainlit_app.show_approvals(SimpleNamespace(payload={}))
        await records.save_decision(
            request.approval_request_id,
            DecisionRecord("u-pm-01", "procurement_manager", "reject", request.requested_at),
        )
        await chainlit_app.review_approval(sent[0].actions[0])

    asyncio.run(scenario())

    assert sent[-1].actions == []
    assert "This approval is closed" in sent[-1].content
    assert session.get("pending_approval") is None


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
    assert session.get("case_id") == "case-1"

    asyncio.run(chainlit_app.on_message(SimpleNamespace(content="Why was Helios rejected?")))

    assert fake.follow_ups == [("case-1", "Why was Helios rejected?")]


def test_reload_without_a_waiting_approval_shows_nothing(session, monkeypatch, sent) -> None:
    done = _case("case-2", CaseStatus.COMPLETED, datetime(2026, 4, 3, tzinfo=UTC))
    fake = _FakeService(cases=(done,))
    monkeypatch.setattr(chainlit_app, "_service", fake)

    asyncio.run(chainlit_app._restore_waiting_approval(_authorization()))

    assert fake.resumed == []
    assert sent == []


def test_open_latest_case_can_show_completed_report(session, monkeypatch, sent) -> None:
    done = _case("case-2", CaseStatus.COMPLETED, datetime(2026, 4, 3, tzinfo=UTC))
    report = UIEvent(
        case_id=done.case_id,
        event_type=UIEventType.PROGRESS,
        stage="resume",
        message="Persisted incident report",
        occurred_at=done.updated_at,
    )
    fake = _FakeService(cases=(done,), resume=report)
    monkeypatch.setattr(chainlit_app, "_service", fake)

    asyncio.run(chainlit_app.open_latest_case(SimpleNamespace(payload={})))

    assert session.get("case_id") == done.case_id
    assert session.get("pending_approval") is None
    assert sent[-1].content == "Persisted incident report"


def test_start_new_case_clears_the_restored_case(session, sent) -> None:
    session.set("case_id", "case-1")
    asyncio.run(chainlit_app.start_new_case(SimpleNamespace(payload={})))

    assert session.get("case_id") is None
    assert session.get("pending_approval") is None


def _request_with_quantity() -> ApprovalRequest:
    buy = RecoveryAction(
        action_id="ACT-BUY-S004",
        action_type=RecoveryActionType.PURCHASE_ALTERNATIVE,
        payload={"supplier_id": "S004", "component_id": "MC-17", "qty": 3000},
        cost_eur=Decimal("29400.00"),
    )
    return _request().model_copy(update={"actions": (_request().actions[0], buy)})


def _reply(monkeypatch: pytest.MonkeyPatch, text: str | None) -> None:
    class _FakeAsk:
        def __init__(self, content: str = "", timeout: int = 0) -> None:
            self.content = content

        async def send(self) -> dict | None:
            return None if text is None else {"output": text}

    monkeypatch.setattr(chainlit_app.cl, "AskUserMessage", _FakeAsk)


def test_editable_request_offers_the_modify_button(session, sent) -> None:
    asyncio.run(chainlit_app._prompt_approval(_request_with_quantity(), "Approval needed."))

    assert [action.name for action in sent[0].actions] == [
        "approve_request",
        "reject_request",
        "modify_request",
    ]


def test_expedite_quantity_is_fixed_by_the_purchase_order() -> None:
    request = _request_with_quantity()
    expedite = request.actions[0]
    expedite = expedite.model_copy(update={"payload": {**expedite.payload, "qty": 1500}})
    request = request.model_copy(update={"actions": (expedite, request.actions[1])})

    assert expedite.action_id not in chainlit_app.quantity_question(request)
    with pytest.raises(ValueError, match="uses the existing purchase order quantity"):
        chainlit_app.modified_actions(request, f"{expedite.action_id} 1000")


def test_modification_changes_only_the_named_quantity() -> None:
    changed = chainlit_app.modified_actions(_request_with_quantity(), "ACT-BUY-S004 2,500")

    assert [a.payload.get("qty") for a in changed] == [None, 2500]
    assert changed[0] == _request_with_quantity().actions[0]  # other actions untouched


@pytest.mark.parametrize(
    "reply, problem",
    [
        ("buy more please", "write the action ID"),
        ("ACT-BUY-S004 2.5", "whole-unit quantity"),
        ("ACT-BUY-S004 25,0", "whole-unit quantity"),
        ("ACT-BUY-S004 2.500", "whole-unit quantity"),
        ("ACT-NOPE 100", "no action ACT-NOPE"),
        ("act-1 100", "has no quantity"),
        ("ACT-BUY-S004 0", "must be positive"),
        ("ACT-BUY-S004 3000", "already has quantity"),
    ],
)
def test_unusable_modification_replies_are_refused(reply: str, problem: str) -> None:
    with pytest.raises(ValueError, match=problem):
        chainlit_app.modified_actions(_request_with_quantity(), reply)


def test_modify_submits_a_non_approval_with_the_changed_actions(
    session, service, sent, monkeypatch
) -> None:
    session.set("pending_approval", _request_with_quantity().model_dump(mode="json"))
    _reply(monkeypatch, "ACT-BUY-S004 2500")

    asyncio.run(chainlit_app.modify_request(_button("req-1")))

    [decision] = service.decisions
    assert decision.is_modification and not decision.approved
    assert decision.modified_actions[1].payload["qty"] == 2500
    assert session.get("pending_approval") is None


def test_modify_without_a_reply_changes_nothing(session, service, sent, monkeypatch) -> None:
    session.set("pending_approval", _request_with_quantity().model_dump(mode="json"))
    _reply(monkeypatch, None)

    asyncio.run(chainlit_app.modify_request(_button("req-1")))

    assert service.decisions == []
    assert "No change was made" in sent[-1].content
    assert session.get("pending_approval") is not None


def test_modify_for_an_old_request_is_refused(session, service, sent, monkeypatch) -> None:
    _reply(monkeypatch, "ACT-BUY-S004 2500")

    asyncio.run(chainlit_app.modify_request(_button("req-old")))

    assert service.decisions == []
    assert sent[0].content == STALE_MESSAGE
