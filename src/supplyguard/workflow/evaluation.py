"""Evaluators for the workflow's categories in the 105-case harness: ambiguity, follow-up
and restart/recovery. Each implements the harness's CaseEvaluator interface.

    evaluators[EvaluationCategory.AMBIGUITY] = AmbiguityEvaluator(model, master_data)
    evaluators[EvaluationCategory.FOLLOW_UP] = FollowUpEvaluator(new_workflow, owner, approvers)
    evaluators[EvaluationCategory.RESTART_RECOVERY] = RestartEvaluator(new_workflow, owner, ...)

`new_workflow(checkpointer)` builds a fresh CaseWorkflow on that checkpointer; calling it twice
on the same checkpointer is what an application restart looks like.
"""

import re
import uuid
from collections.abc import AsyncIterator, Callable, Sequence
from datetime import UTC, datetime

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver

from supplyguard.contracts import (
    ApprovalDecision,
    AuthorizationContext,
    IncidentInput,
    UIEvent,
    UIEventType,
)
from supplyguard.contracts.events import EvaluationResult, EvaluationStatus
from supplyguard.contracts.protocols import MasterDataService
from supplyguard.evaluation.cases import EvaluationCase
from supplyguard.workflow.intake.extraction import extract
from supplyguard.workflow.intake.nodes import resolve
from supplyguard.workflow.service import CaseWorkflow

GOLDEN_MESSAGE = (
    "Supplier Atlas Components has announced a 12-day delay for component MC-17. Determine "
    "which customer commitments are at risk, investigate alternative sources, and recommend a "
    "recovery plan. Additional spending must not exceed €75,000. Do not create a purchase "
    "order without approval."
)
KILL_STEP = re.compile(r"kill application at step '(\w+)'")

NewWorkflow = Callable[[BaseCheckpointSaver], CaseWorkflow]


def _result(case: EvaluationCase, problems: list[str], **metrics: float) -> EvaluationResult:
    return EvaluationResult(
        evaluation_case_id=case.id,
        category=case.category.value,
        status=EvaluationStatus.FAILED if problems else EvaluationStatus.PASSED,
        metrics=metrics,
        findings=tuple(problems),
    )


async def _collect(stream: AsyncIterator[UIEvent], stop_after: str | None = None) -> list:
    """All events, or only up to the given step: stopping there is what a crash looks like."""
    events = []
    try:
        async for event in stream:
            events.append(event)
            if event.stage == stop_after:
                break
    finally:
        await stream.aclose()  # a stopped run does not continue in the background
    return events


class AmbiguityEvaluator:
    """Passes when intake asks a question instead of confirming a supplier and component."""

    def __init__(self, model: BaseChatModel, master_data: MasterDataService) -> None:
        self.model = model
        self.master_data = master_data

    async def evaluate(self, case: EvaluationCase) -> EvaluationResult:
        extraction = await extract(self.model, [HumanMessage(case.message)])
        supplier, component, questions = await resolve(extraction, self.master_data)
        problems = (
            []
            if questions
            else [
                f"guessed {supplier.supplier_id if supplier else '?'} / "
                f"{component.component_id if component else '?'} without asking"
            ]
        )
        return _result(case, problems, questions_asked=float(len(questions)))


class _GoldenCase:
    """Runs the golden MC-17 case to its approval pause, for the cases that depend on it."""

    def __init__(
        self,
        new_workflow: NewWorkflow,
        owner: AuthorizationContext,
        approvers: Sequence[AuthorizationContext],
        checkpointer_factory: Callable[[], BaseCheckpointSaver] = InMemorySaver,
    ) -> None:
        self.new_workflow = new_workflow
        self.owner = owner
        self.approvers = tuple(approvers) or (owner,)
        self.checkpointer_factory = checkpointer_factory

    def incident(self, case: EvaluationCase) -> IncidentInput:
        case_id = f"{case.id}-{uuid.uuid4().hex[:6]}"
        return IncidentInput(case_id=case_id, user_id=self.owner.user_id, message=GOLDEN_MESSAGE)

    async def approve_all(self, workflow: CaseWorkflow, request: dict) -> list[UIEvent]:
        """Every required approver approves the exact request, one decision each."""
        events: list[UIEvent] = []
        for approver in self.approvers:
            if approver.role not in request["required_approver_roles"]:
                continue
            decision = ApprovalDecision(
                approval_request_id=request["approval_request_id"],
                case_id=request["case_id"],
                payload_hash=request["payload_hash"],
                approved=True,
                approver_user_ids=(approver.user_id,),
                approver_roles=(approver.role,),
                decided_at=datetime.now(UTC),
            )
            events += await _collect(workflow.submit_approval(decision, approver))
        return events


class FollowUpEvaluator(_GoldenCase):
    """Asks the follow-up while the golden case waits for approval: the answer must come from
    the saved case, and asking must not decide or execute anything."""

    async def evaluate(self, case: EvaluationCase) -> EvaluationResult:
        workflow = self.new_workflow(self.checkpointer_factory())
        incident = self.incident(case)
        before = await _collect(workflow.start_case(incident, self.owner))
        answer = await _collect(workflow.send_message(incident.case_id, case.message, self.owner))
        after = [c for c in await workflow.list_cases(self.owner) if c.case_id == incident.case_id]

        problems = []
        if not before or before[-1].event_type is not UIEventType.APPROVAL:
            problems.append("the golden case did not reach approval")
        if not answer or not answer[-1].message.strip():
            problems.append("no answer")
        if not after or after[0].stage != "await_approval":
            problems.append("asking a question changed the case")
        if "90,000" in case.message and answer and "EUR 90,000" not in answer[-1].message:
            problems.append("the new limit was not evaluated by rerunning the planner")
        if (
            "resume" in case.message.lower()
            and answer
            and incident.case_id not in answer[-1].message
        ):
            problems.append("the recap does not name the case")
        return _result(case, problems)


class RestartEvaluator(_GoldenCase):
    """Stops the golden case at the step named in the message, builds a new workflow on the
    same checkpoints (the restart), resumes, and checks that the case finishes with each
    action executed once and with the same approval payload."""

    async def evaluate(self, case: EvaluationCase) -> EvaluationResult:
        match = KILL_STEP.search(case.message)
        if not match:
            return _result(case, ["no step to stop at in the case message"])
        step = match.group(1)
        checkpointer = self.checkpointer_factory()
        incident = self.incident(case)

        first = self.new_workflow(checkpointer)
        stop = "investigate_impact" if step == "after_graph_query" else None
        events = await _collect(first.start_case(incident, self.owner), stop_after=stop)
        request = events[-1].payload.get("request") if stop is None else None
        if step == "before_execute" and request:
            await self._approve_until(first, request, stop_after="record_decision")

        restarted = self.new_workflow(checkpointer)  # a new process on the saved checkpoints
        events = await _collect(restarted.resume_case(incident.case_id, self.owner))
        problems = []
        if events and events[-1].event_type is UIEventType.APPROVAL:
            resumed = events[-1].payload["request"]
            if request and resumed["payload_hash"] != request["payload_hash"]:
                problems.append("the approval payload changed across the restart")
            events = await self.approve_all(restarted, resumed)

        state = (await restarted._graph.aget_state(restarted._config(incident.case_id))).values
        results = state.get("execution_results") or []
        keys = [r["idempotency_key"] for r in results]
        if state.get("status") != "completed":
            problems.append(f"the case ended as {state.get('status')}, not completed")
        if len(keys) != len(set(keys)):
            problems.append("an action was executed twice")
        if any(r["status"] not in ("succeeded", "already_executed") for r in results):
            problems.append("an approved action did not complete")
        return _result(case, problems, executed_actions=float(len(results)))

    async def _approve_until(self, workflow: CaseWorkflow, request: dict, stop_after: str):
        """Submit the approvals but stop after the last decision is recorded: the crash
        happens before execution."""
        required = [a for a in self.approvers if a.role in request["required_approver_roles"]]
        for index, approver in enumerate(required):
            decision = ApprovalDecision(
                approval_request_id=request["approval_request_id"],
                case_id=request["case_id"],
                payload_hash=request["payload_hash"],
                approved=True,
                approver_user_ids=(approver.user_id,),
                approver_roles=(approver.role,),
                decided_at=datetime.now(UTC),
            )
            last = index == len(required) - 1
            await _collect(
                workflow.submit_approval(decision, approver), stop_after if last else None
            )
