"""Approval and execution steps. The rules (tiers, hashing, revalidation, idempotency) are the
approval and procurement services' job; the workflow pauses, routes, and never executes
anything that was not approved for the exact payload."""

from langchain_core.messages import AIMessage
from langgraph.types import interrupt

from supplyguard.contracts import (
    ApprovalDecision,
    ApprovalRequest,
    CaseStatus,
    ExecutionResult,
    ExecutionStatus,
    RecoveryPlan,
)
from supplyguard.workflow.services import WorkflowServices
from supplyguard.workflow.state import CaseState


def _request(state: CaseState) -> ApprovalRequest:
    return ApprovalRequest.model_validate(state["approval_request"])


def _decision(state: CaseState) -> ApprovalDecision:
    return ApprovalDecision.model_validate(state["decision"])


def make_approval_steps(services: WorkflowServices) -> dict:
    async def request_approval(state: CaseState) -> dict:
        """Built in its own step, before the pause, so a restart shows the same payload."""
        request = await services.approval.create_request(RecoveryPlan.model_validate(state["plan"]))
        return {
            "approval_request": request.model_dump(mode="json"),
            "status": CaseStatus.AWAITING_APPROVAL.value,
        }

    def await_approval(state: CaseState) -> dict:
        """Only the pause and a check. LangGraph runs it again from the top on resume."""
        request = _request(state)
        answer = interrupt({"type": "approval", "request": request.model_dump(mode="json")})
        decision = ApprovalDecision.model_validate(answer)
        same_payload = (
            decision.case_id == request.case_id
            and decision.approval_request_id == request.approval_request_id
            and decision.payload_hash == request.payload_hash
        )
        problem = None if same_payload else "This decision is for a different approval request."
        update = {"decision": decision.model_dump(mode="json"), "decision_problem": problem}
        if problem:
            update["messages"] = [AIMessage(f"{problem} Please review the current request.")]
        return update

    async def record_decision(state: CaseState) -> dict:
        try:
            recorded = await services.approval.record_decision(_request(state), _decision(state))
        except ValueError as error:  # e.g. the approvers do not cover the required roles
            return {
                "decision_problem": str(error),
                "messages": [AIMessage(f"The decision was not accepted: {error}")],
            }
        return {"decision": recorded.model_dump(mode="json")}

    async def revalidate(state: CaseState) -> dict:
        """A modification never counts as approval: it becomes a new request to decide on.

        If the edit breaks a rule (e.g. more than the supplier's capacity), the current request
        stays, the reason is shown, and the case returns to the approval pause.
        """
        try:
            new_request = await services.approval.revalidate(
                _request(state), _decision(state).modified_actions
            )
        except ValueError as error:  # RevalidationError is a ValueError
            text = f"The change could not be accepted: {error}. The current request still stands."
            return {"messages": [AIMessage(text)], "decision": None}
        return {"approval_request": new_request.model_dump(mode="json"), "decision": None}

    async def execute(state: CaseState) -> dict:
        try:
            results = await services.execution.execute(_request(state), _decision(state))
        except Exception as error:
            return {"execution_results": [], "errors": [f"Execution failed: {error}"]}
        return {"execution_results": [r.model_dump(mode="json") for r in results]}

    return {
        "request_approval": request_approval,
        "await_approval": await_approval,
        "record_decision": record_decision,
        "revalidate": revalidate,
        "execute": execute,
    }


def route_after_await(state: CaseState) -> str:
    return "await_approval" if state.get("decision_problem") else "record_decision"


def route_after_record(state: CaseState) -> str:
    if state.get("decision_problem"):
        return "await_approval"
    decision = _decision(state)
    if decision.is_modification:
        return "revalidate"
    return "execute" if decision.approved else "finish"


def finish(state: CaseState) -> dict:
    """A short closing message; the full incident report comes in the reporting step."""
    if state.get("execution_results") is None:  # rejected: never reached execution
        text = "The recommendation was rejected, so nothing was executed."
        return {"messages": [AIMessage(text)], "status": CaseStatus.COMPLETED.value}

    results = [ExecutionResult.model_validate(r) for r in state["execution_results"]]
    done = (ExecutionStatus.SUCCEEDED, ExecutionStatus.ALREADY_EXECUTED)
    failed = [r for r in results if r.status not in done]
    lines = [f"- {r.action_type.value}: {r.status.value} - {r.message}" for r in results]
    lines += [f"- {e}" for e in state.get("errors") or []]
    if failed or not results:
        text = "Some approved actions could not be completed:\n" + "\n".join(lines)
        return {"messages": [AIMessage(text)], "status": CaseStatus.FAILED.value}
    text = "The approved actions were carried out:\n" + "\n".join(lines)
    return {"messages": [AIMessage(text)], "status": CaseStatus.COMPLETED.value}
