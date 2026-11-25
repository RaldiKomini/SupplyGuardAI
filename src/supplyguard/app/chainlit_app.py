"""Chainlit entry point: run with `chainlit run src/supplyguard/app/chainlit_app.py`."""

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
    IncidentInput,
    UIEvent,
    UIEventType,
)
from supplyguard.workflow.checkpoints import use_windows_compatible_event_loop
from supplyguard.workflow.runtime import open_case_workflow

use_windows_compatible_event_loop()

_exit_stack: AsyncExitStack | None = None
_service: CaseService | None = None
# Failures the user can fix by changing the request; they are shown in the chat.
USER_ERRORS = (CaseNotFoundError, LookupError, PermissionError, RequestRejectedError, ValueError)


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
    except Exception as error:
        await stack.aclose()
        raise SystemExit(f"The case workflow could not start: {error}") from error
    _exit_stack, _service = stack, CaseService(workflow)


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
        "Describe a supplier disruption to open a case."
    ).send()


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
    cl.user_session.set("pending_approval", None)
    try:
        decision = build_decision(pending, authorization, approved)
        await _show(await _service.submit_approval(decision, authorization))
    except USER_ERRORS as error:
        cl.user_session.set("pending_approval", pending)
        await cl.Message(f"The decision could not be recorded: {error}").send()


def build_decision(
    pending: dict, authorization: AuthorizationContext, approved: bool
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
    )


def approval_summary(request: ApprovalRequest) -> str:
    lines = [
        f"Total cost: €{request.total_cost_eur}",
        f"Required approver roles: {', '.join(request.required_approver_roles)}",
        "Actions:",
    ]
    for action in request.actions:
        line = f"- {action.action_type.value}: €{action.cost_eur}"
        if action.expected_completion_date is not None:
            line += f", expected {action.expected_completion_date}"
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
    request = ApprovalRequest.model_validate(event.payload["request"])
    cl.user_session.set("pending_approval", request.model_dump(mode="json"))
    payload = {"approval_request_id": request.approval_request_id}
    await cl.Message(
        content=f"{event.message}\n\n{approval_summary(request)}",
        actions=[
            cl.Action(name="approve_request", payload=payload, label="Approve"),
            cl.Action(name="reject_request", payload=payload, label="Reject"),
        ],
    ).send()
