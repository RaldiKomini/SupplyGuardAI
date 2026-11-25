"""Chainlit entry point: run with `chainlit run src/supplyguard/app/chainlit_app.py`."""

import json
import logging
import re
import uuid
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack
from datetime import UTC, datetime

import chainlit as cl

from supplyguard.app.case_service import CaseNotFoundError, CaseService, RequestRejectedError
from supplyguard.auth import get_registry, require_auth_secret
from supplyguard.contracts import (
    ApprovalDecision,
    ApprovalRequest,
    AuthorizationContext,
    CaseStatus,
    IncidentInput,
    RecoveryAction,
    UIEvent,
    UIEventType,
)
from supplyguard.workflow.checkpoints import use_windows_compatible_event_loop
from supplyguard.workflow.runtime import open_approval_source, open_case_workflow

use_windows_compatible_event_loop()

_exit_stack: AsyncExitStack | None = None
_service: CaseService | None = None
# Failures the user can fix by changing the request; they are shown in the chat.
USER_ERRORS = (CaseNotFoundError, LookupError, PermissionError, RequestRejectedError, ValueError)
logger = logging.getLogger(__name__)
STALE_REQUEST_MESSAGE = "This approval request is no longer current. Review the latest request."
ACTION_FIELD_LABELS = {
    "arrival_day": "Arrival day",
    "component_id": "Component",
    "days_gain": "Days gained",
    "fee_pct": "Expedite fee (%)",
    "from_location": "From",
    "order_id": "Purchase order",
    "po_id": "Purchase order",
    "qty": "Quantity",
    "supplier_id": "Supplier",
    "to_location": "To",
    "unit_price_eur": "Unit price (EUR)",
}


@cl.on_app_startup
async def start_services() -> None:
    # Chainlit allows one startup hook, so the auth check and the workflow start together.
    global _exit_stack, _service
    try:
        require_auth_secret()
    except RuntimeError as error:
        # Chainlit logs and swallows ordinary exceptions raised by startup hooks.
        raise SystemExit(str(error)) from error
    stack = AsyncExitStack()
    try:
        workflow = await stack.enter_async_context(open_case_workflow())
        approvals = await stack.enter_async_context(open_approval_source())
    except Exception as error:
        await stack.aclose()
        raise SystemExit(f"The case workflow could not start: {error}") from error
    _exit_stack, _service = stack, CaseService(workflow, approvals)


@cl.on_app_shutdown
async def stop_services() -> None:
    global _exit_stack, _service
    if _exit_stack is not None:
        await _exit_stack.aclose()
    _exit_stack, _service = None, None


@cl.data_layer
def no_data_layer():
    # Chainlit would otherwise treat DATABASE_URL as its own persistence layer.
    return None


@cl.password_auth_callback
async def authenticate(username: str, password: str) -> cl.User | None:
    context = get_registry().authenticate(username, password)
    if context is None:
        return None
    return cl.User(identifier=context.user_id, metadata={"role": context.role})


@cl.on_chat_start
async def start() -> None:
    user = cl.user_session.get("user")
    registry = get_registry()
    # Scopes are re-derived from configuration, never taken from the session token.
    context = registry.authorization_for(user.identifier)
    if context is None:
        await cl.Message("Your account has no access configuration.").send()
        return
    cl.user_session.set("authorization", context)
    await cl.Message(
        f"Signed in as {registry.display_name(context.user_id)} ({context.role}). "
        "Describe a supplier disruption to open a case.",
        actions=[
            cl.Action(name="show_approvals", payload={}, label="Show my approvals"),
            cl.Action(name="open_latest_case", payload={}, label="Open latest case"),
            cl.Action(name="start_new_case", payload={}, label="Start new case"),
        ],
    ).send()
    await _restore_waiting_approval(context)


@cl.on_message
async def on_message(message: cl.Message) -> None:
    authorization: AuthorizationContext | None = cl.user_session.get("authorization")
    if authorization is None or _service is None:
        await cl.Message("The session is not ready. Please sign in again.").send()
        return
    case_id: str | None = cl.user_session.get("case_id")
    try:
        if case_id is None:
            # One chat session is one case: the first message opens it.
            case_id = str(uuid.uuid4())
            incident = IncidentInput(
                case_id=case_id, user_id=authorization.user_id, message=message.content
            )
            cl.user_session.set("case_id", case_id)
            await _show(await _service.start_case(incident, authorization))
        else:
            await _show(await _service.send_message(case_id, message.content, authorization))
    except USER_ERRORS as error:
        await cl.Message(f"That request could not be processed: {error}").send()


@cl.action_callback("approve_request")
async def approve_request(action: cl.Action) -> None:
    await _decide(action, approved=True)


@cl.action_callback("reject_request")
async def reject_request(action: cl.Action) -> None:
    await _decide(action, approved=False)


@cl.action_callback("modify_request")
async def modify_request(action: cl.Action) -> None:
    """Change a quantity in the pending request. The change is revalidated by the approval
    service and becomes a new request that needs a fresh approval; nothing is approved here."""
    authorization: AuthorizationContext | None = cl.user_session.get("authorization")
    pending: dict | None = cl.user_session.get("pending_approval")
    if (
        authorization is None
        or _service is None
        or pending is None
        or pending["approval_request_id"] != action.payload.get("approval_request_id")
    ):
        await cl.Message(STALE_REQUEST_MESSAGE).send()
        return
    request = ApprovalRequest.model_validate(pending)
    reply = await cl.AskUserMessage(content=quantity_question(request), timeout=300).send()
    if not reply:
        await cl.Message("No change was made. The current request still stands.").send()
        return
    try:
        changed = modified_actions(request, str(reply.get("output", "")))
    except ValueError as error:
        await cl.Message(f"No change was made: {error}").send()
        return
    cl.user_session.set("pending_approval", None)
    try:
        decision = build_decision(pending, authorization, False, modified_actions=changed)
        await _show(await _service.submit_approval(decision, authorization))
    except USER_ERRORS as error:
        cl.user_session.set("pending_approval", pending)
        await cl.Message(f"The change could not be recorded: {error}").send()
    except Exception:
        logger.exception("Modification failed for request %s", request.approval_request_id)
        cl.user_session.set("pending_approval", pending)
        await cl.Message("The change could not be recorded. Please try again.").send()


@cl.action_callback("start_new_case")
async def start_new_case(action: cl.Action) -> None:
    cl.user_session.set("case_id", None)
    cl.user_session.set("pending_approval", None)
    await cl.Message("Describe a supplier disruption to open a new case.").send()


@cl.action_callback("open_latest_case")
async def open_latest_case(action: cl.Action) -> None:
    authorization: AuthorizationContext | None = cl.user_session.get("authorization")
    if authorization is None or _service is None:
        await cl.Message("The session is not ready. Please sign in again.").send()
        return
    try:
        cases = await _service.list_cases(authorization)
        if not cases:
            await cl.Message("You have no cases yet. Describe a disruption to open one.").send()
            return
        latest = max(cases, key=lambda case: case.updated_at)
        await _resume_owned_case(_service, latest.case_id, authorization)
    except Exception:
        logger.exception("Latest case could not be opened for %s", authorization.user_id)
        await cl.Message("The case could not be opened. Please try again.").send()


@cl.action_callback("show_approvals")
async def show_approvals(action: cl.Action) -> None:
    authorization: AuthorizationContext | None = cl.user_session.get("authorization")
    if authorization is None or _service is None:
        await cl.Message("The session is not ready. Please sign in again.").send()
        return
    try:
        requests = await _service.list_open_approvals(authorization)
    except Exception:
        logger.exception("Open approvals could not be listed for %s", authorization.user_id)
        await cl.Message("Approvals could not be loaded. Please try again.").send()
        return
    if not requests:
        await cl.Message("No approvals are waiting for your role.").send()
        return
    for request in requests:
        await cl.Message(
            content=f"Approval waiting for {', '.join(request.required_approver_roles)}: "
            f"case {request.case_id}, total €{request.total_cost_eur}.",
            actions=[
                cl.Action(
                    name="review_approval",
                    payload={"approval_request_id": request.approval_request_id},
                    label="Review",
                )
            ],
        ).send()


@cl.action_callback("review_approval")
async def review_approval(action: cl.Action) -> None:
    authorization: AuthorizationContext | None = cl.user_session.get("authorization")
    if authorization is None or _service is None:
        await cl.Message("The session is not ready. Please sign in again.").send()
        return
    request_id = str(action.payload.get("approval_request_id", ""))
    try:
        request = await _service.get_approval_request(request_id, authorization)
        open_requests = await _service.list_open_approvals(authorization)
        if not any(item.approval_request_id == request_id for item in open_requests):
            await cl.Message(
                "This approval is closed or is no longer waiting for your decision."
            ).send()
            return
        await _prompt_approval(request, "Review this approval request.")
    except USER_ERRORS as error:
        await cl.Message(f"That approval could not be opened: {error}").send()
    except Exception:
        logger.exception("Approval request %s could not be opened", request_id)
        await cl.Message("The approval could not be opened. Please try again.").send()


async def _decide(action: cl.Action, *, approved: bool) -> None:
    authorization: AuthorizationContext | None = cl.user_session.get("authorization")
    pending: dict | None = cl.user_session.get("pending_approval")
    # The decision uses the request stored on the server, never a payload sent by the browser.
    if (
        authorization is None
        or _service is None
        or pending is None
        or pending["approval_request_id"] != action.payload.get("approval_request_id")
    ):
        await cl.Message(
            "This approval request is no longer current. Review the latest request."
        ).send()
        return
    request_id = pending["approval_request_id"]
    cl.user_session.set("pending_approval", None)
    try:
        decision = build_decision(pending, authorization, approved)
        await _show(await _service.submit_approval(decision, authorization))
    except USER_ERRORS as error:
        cl.user_session.set("pending_approval", pending)
        await cl.Message(f"The decision could not be recorded: {error}").send()
    except Exception:
        logger.exception("Approval decision failed for request %s", request_id)
        cl.user_session.set("pending_approval", pending)
        await cl.Message("The decision could not be recorded. Please try again.").send()


def build_decision(
    pending: dict,
    authorization: AuthorizationContext,
    approved: bool,
    *,
    modified_actions: tuple[RecoveryAction, ...] = (),
) -> ApprovalDecision:
    """The decision names only the signed-in user and role, as CaseService requires."""
    return ApprovalDecision(
        approval_request_id=pending["approval_request_id"],
        case_id=pending["case_id"],
        payload_hash=pending["payload_hash"],
        approved=approved,
        approver_user_ids=(authorization.user_id,),
        approver_roles=(authorization.role,),
        decided_at=datetime.now(UTC),
        modified_actions=modified_actions,
    )


_QUANTITY_CHANGE = re.compile(r"^\s*(\S+)\s+(?:qty\s*=?\s*)?(\d[\d,.]*)\s*$", re.IGNORECASE)


def quantity_question(request: ApprovalRequest) -> str:
    choices = [
        f"{a.action_id} (now {a.payload['qty']})" for a in request.actions if "qty" in a.payload
    ]
    example = request.actions[-1].action_id if request.actions else "ACT-1"
    return (
        "Which quantity should change? Reply with the action ID and the new quantity, "
        f"for example: {example} 2500.\nQuantities you can change: {', '.join(choices)}."
    )


def modified_actions(request: ApprovalRequest, text: str) -> tuple[RecoveryAction, ...]:
    """The request's actions with one quantity changed, from a reply like "ACT-BUY-S004 2500".
    Prices and checks are redone by the approval service, never here."""
    match = _QUANTITY_CHANGE.match(text)
    if not match:
        raise ValueError("write the action ID and the new quantity, for example ACT-BUY-S004 2500")
    action_id = match.group(1)
    quantity = int(match.group(2).replace(",", "").replace(".", ""))
    target = next((a for a in request.actions if a.action_id == action_id), None)
    if target is None:
        raise ValueError(f"there is no action {action_id} in this request")
    if "qty" not in target.payload:
        raise ValueError(f"{action_id} has no quantity to change")
    if quantity <= 0:
        raise ValueError("the quantity must be positive")
    if quantity == int(target.payload["qty"]):
        raise ValueError(f"{action_id} already has quantity {quantity}")
    return tuple(
        a.model_copy(update={"payload": {**a.payload, "qty": quantity}})
        if a.action_id == action_id
        else a
        for a in request.actions
    )


def _markdown_text(value: str) -> str:
    return re.sub(r"([\\`*_{}\[\]()<>#!|~$])", r"\\\1", " ".join(value.splitlines()))


def approval_summary(request: ApprovalRequest) -> str:
    lines = [
        f"Total cost: €{request.total_cost_eur}",
        f"Required approver roles: {', '.join(request.required_approver_roles)}",
        f"Approval version: {request.payload_version}",
        "Actions:",
    ]
    for action in request.actions:
        line = f"- {action.action_type.value}: €{action.cost_eur}"
        if action.expected_completion_date is not None:
            line += f", expected {action.expected_completion_date}"
        fields = []
        for key, value in action.payload.items():
            label = ACTION_FIELD_LABELS.get(key, key.replace("_", " ").capitalize())
            display = value if isinstance(value, str) else json.dumps(value, default=str)
            fields.append(f"{_markdown_text(label)}: {_markdown_text(display)}")
        if fields:
            line += " · " + " · ".join(fields)
        lines.append(line)
    return "\n".join(lines)


async def _show(events: AsyncIterator[UIEvent]) -> None:
    async for event in events:
        if event.event_type is UIEventType.APPROVAL:
            await _ask_for_approval(event)
        elif event.payload.get("report"):
            await cl.Message(content=str(event.payload["report"])).send()
        else:
            await cl.Message(content=event.message).send()


async def _ask_for_approval(event: UIEvent) -> None:
    await _prompt_approval(ApprovalRequest.model_validate(event.payload["request"]), event.message)


async def _prompt_approval(request: ApprovalRequest, intro: str) -> None:
    authorization: AuthorizationContext | None = cl.user_session.get("authorization")
    summary = f"{intro}\n\n{approval_summary(request)}"
    roles = ", ".join(request.required_approver_roles)
    if authorization is None or authorization.role not in request.required_approver_roles:
        cl.user_session.set("pending_approval", None)
        await cl.Message(
            content=f"{summary}\n\nWaiting for approval from {roles}. "
            "Your role cannot approve this request."
        ).send()
        return
    # The store knows who has decided, so a page reload cannot offer buttons a second time.
    if _service is not None and await _service.has_decided(
        request.approval_request_id, authorization
    ):
        others = [role for role in request.required_approver_roles if role != authorization.role]
        cl.user_session.set("pending_approval", None)
        await cl.Message(
            content=f"Your approval is recorded. Waiting for approval from {', '.join(others)}."
        ).send()
        return
    cl.user_session.set("pending_approval", request.model_dump(mode="json"))
    payload = {"approval_request_id": request.approval_request_id}
    await cl.Message(
        content=summary,
        actions=[
            cl.Action(name="approve_request", payload=payload, label="Approve"),
            cl.Action(name="reject_request", payload=payload, label="Reject"),
            cl.Action(name="modify_request", payload=payload, label="Modify quantity"),
        ],
    ).send()


async def _restore_waiting_approval(authorization: AuthorizationContext) -> None:
    """A page reload drops the buttons, so the latest waiting approval is shown again."""
    if _service is None:
        return
    try:
        cases = await _service.list_cases(authorization)
        waiting = [case for case in cases if case.status is CaseStatus.AWAITING_APPROVAL]
        if not waiting:
            return
        latest = max(waiting, key=lambda case: case.updated_at)
        await _resume_owned_case(_service, latest.case_id, authorization)
    except Exception:
        logger.exception("Waiting approvals could not be restored for %s", authorization.user_id)
        await cl.Message("Waiting approvals could not be loaded. Please reload the page.").send()


async def _resume_owned_case(
    service: CaseService, case_id: str, authorization: AuthorizationContext
) -> None:
    events = await service.resume_case(case_id, authorization)
    cl.user_session.set("case_id", case_id)
    cl.user_session.set("pending_approval", None)
    await _show(events)
