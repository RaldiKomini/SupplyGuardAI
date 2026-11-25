"""CaseWorkflow: the CaseWorkflowService the application layer calls.

Runs the graph and turns each finished step into a UIEvent. Case access is owner-only.
Designated approvers may submit decisions without opening the case.
"""

from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.types import Command

from supplyguard.contracts import (
    ApprovalDecision,
    ApprovalRequest,
    AuthorizationContext,
    CaseStatus,
    CaseSummary,
    ConfirmedIncident,
    IncidentInput,
    OperationalIntelligenceResult,
    UIEvent,
    UIEventType,
)
from supplyguard.workflow.follow_up import answer_follow_up
from supplyguard.workflow.graph import build_graph
from supplyguard.workflow.services import WorkflowServices

# What each step shows on screen: (event type, default message, state keys sent as payload).
STEP_EVENTS = {
    "read_incident": (
        UIEventType.PROGRESS,
        "Incident details read.",
        ("incident", "security_findings"),
    ),
    "investigate_impact": (UIEventType.PROGRESS, "Operational impact analysed.", ("intelligence",)),
    "collect_evidence": (UIEventType.EVIDENCE, "Documentary evidence collected.", ("evidence",)),
    "deep_investigation": (
        UIEventType.EVIDENCE,
        "Extended investigation finished.",
        ("investigation_findings",),
    ),
    "plan_recovery": (UIEventType.OPTIONS, "Recovery options compared.", ("plan",)),
    "record_decision": (UIEventType.PROGRESS, "Decision recorded.", ()),
    "revalidate": (UIEventType.PROGRESS, "Modified request revalidated.", ()),
    "execute": (UIEventType.OUTCOME, "Approved actions sent.", ("execution_results",)),
    "finish": (UIEventType.OUTCOME, "Case closed.", ()),
    "write_report": (UIEventType.OUTCOME, "Incident report ready.", ("report",)),
    "stop_case": (UIEventType.ERROR, "No recommendation could be prepared.", ()),
    "stop_intake": (UIEventType.PROGRESS, "More information is needed.", ()),
    "follow_up": (UIEventType.PROGRESS, "Answer.", ("security_findings",)),
}


def _event(case_id: str, kind: UIEventType, stage: str, message: str, payload=None) -> UIEvent:
    return UIEvent(
        case_id=case_id,
        event_type=kind,
        stage=stage,
        message=message,
        occurred_at=datetime.now(UTC),
        payload=payload or {},
    )


class CaseWorkflow:
    """Implements supplyguard.contracts.protocols.CaseWorkflowService."""

    def __init__(
        self, *, model: BaseChatModel, services: WorkflowServices, checkpointer: BaseCheckpointSaver
    ) -> None:
        self._model = model
        self._services = services
        self._checkpointer = checkpointer
        self._graph = build_graph(model=model, services=services, checkpointer=checkpointer)

    @staticmethod
    def _config(case_id: str) -> dict:
        return {"configurable": {"thread_id": case_id}}

    async def _owned_state(self, case_id: str, authorization: AuthorizationContext):
        state = await self._graph.aget_state(self._config(case_id))
        if not state.values:
            raise LookupError(f"unknown case {case_id}")
        if state.values.get("user_id") != authorization.user_id:
            raise PermissionError("this case belongs to another user")
        return state

    @staticmethod
    def _pending(state) -> dict | None:
        """The question or approval the case is waiting for, if any."""
        interrupts = [i for task in state.tasks for i in task.interrupts]
        return interrupts[0].value if interrupts else None

    @staticmethod
    def _pause_event(case_id: str, pending: dict) -> UIEvent:
        if pending["type"] == "approval":
            return _event(
                case_id,
                UIEventType.APPROVAL,
                "await_approval",
                "Approval needed for the exact request below.",
                pending,
            )
        return _event(
            case_id, UIEventType.PROGRESS, "ask_clarification", pending["question"], pending
        )

    async def _run(self, case_id: str, graph_input) -> AsyncIterator[UIEvent]:
        config = self._config(case_id)
        # "sync": each step is saved before the next one starts. With LangGraph's default
        # ("async") a crash right after a step can lose its save, and a restart would run that
        # step again (e.g. record the same approval twice).
        stream = self._graph.astream(graph_input, config, stream_mode="updates", durability="sync")
        async for chunk in stream:
            for step, update in chunk.items():
                if step == "__interrupt__":
                    for pause in update:
                        yield self._pause_event(case_id, pause.value)
                    continue
                if step not in STEP_EVENTS or not update:
                    continue
                kind, default, keys = STEP_EVENTS[step]
                said = [m.content for m in update.get("messages", []) if isinstance(m, AIMessage)]
                payload = {k: update[k] for k in keys if update.get(k) is not None}
                yield _event(case_id, kind, step, str(said[-1]) if said else default, payload)

    async def start_case(
        self, incident: IncidentInput, authorization: AuthorizationContext
    ) -> AsyncIterator[UIEvent]:
        if incident.user_id != authorization.user_id:
            raise PermissionError("a case can only be started for the signed-in user")
        if (await self._graph.aget_state(self._config(incident.case_id))).values:
            raise ValueError(f"case {incident.case_id} already exists")
        first = {
            "case_id": incident.case_id,
            "user_id": incident.user_id,
            "authorization": authorization.model_dump(mode="json"),
            "messages": [HumanMessage(incident.message)],
        }
        async for event in self._run(incident.case_id, first):
            yield event

    async def send_message(
        self, case_id: str, message: str, authorization: AuthorizationContext
    ) -> AsyncIterator[UIEvent]:
        state = await self._owned_state(case_id, authorization)
        pending = self._pending(state)
        if pending and pending["type"] == "clarification":  # the answer resumes intake
            stream = self._run(case_id, Command(resume=message))
        elif pending:  # waiting for approval: answer, but keep waiting
            answer, findings = await answer_follow_up(
                self._model, self._services, state.values, message
            )
            payload = {"security_findings": [f.model_dump(mode="json") for f in findings]}
            yield _event(case_id, UIEventType.PROGRESS, "follow_up", answer, payload)
            return
        else:  # finished case -> follow-up; unfinished intake -> read again
            stream = self._run(case_id, {"messages": [HumanMessage(message)]})
        async for event in stream:
            yield event

    async def resume_case(
        self, case_id: str, authorization: AuthorizationContext
    ) -> AsyncIterator[UIEvent]:
        state = await self._owned_state(case_id, authorization)
        pending = self._pending(state)
        if pending:
            yield self._pause_event(case_id, pending)
            return
        if state.next:
            async for event in self._run(case_id, None):
                yield event
            return
        said = [m.content for m in state.values.get("messages", []) if isinstance(m, AIMessage)]
        yield _event(
            case_id,
            UIEventType.PROGRESS,
            "resume",
            str(said[-1]) if said else "Case loaded.",
            {"status": state.values.get("status")},
        )

    async def submit_approval(
        self, decision: ApprovalDecision, authorization: AuthorizationContext
    ) -> AsyncIterator[UIEvent]:
        if decision.approver_user_ids != (authorization.user_id,) or decision.approver_roles != (
            authorization.role,
        ):
            raise PermissionError("the decision must name only the signed-in user and role")
        state = await self._graph.aget_state(self._config(decision.case_id))
        owns_case = state.values.get("user_id") == authorization.user_id
        pending = self._pending(state)
        if not pending or pending["type"] != "approval":
            if not owns_case:
                raise PermissionError("no approval is available to this user")
            raise ValueError("this case is not waiting for an approval")
        request = ApprovalRequest.model_validate(pending["request"])
        if authorization.role not in request.required_approver_roles:
            raise PermissionError("no approval is available to this user")
        if (
            decision.case_id != request.case_id
            or decision.approval_request_id != request.approval_request_id
            or decision.payload_hash != request.payload_hash
        ):
            raise ValueError("the decision does not match the current approval request")
        async for event in self._run(
            decision.case_id, Command(resume=decision.model_dump(mode="json"))
        ):
            if owns_case:
                yield event
            elif event.stage in ("record_decision", "revalidate"):
                # A designated approver has no access to case results or the final report.
                yield _event(
                    decision.case_id, UIEventType.PROGRESS, event.stage, "Decision submitted."
                )

    async def case_values(self, case_id: str) -> dict:
        """Everything saved in a case, for internal use (evaluation, the approval service).
        It does no ownership check, so it must not be exposed to users."""
        return dict((await self._graph.aget_state(self._config(case_id))).values)

    async def case_context(self, case_id: str):
        """(ConfirmedIncident, ImpactReport) saved in a case. The approval service uses it to
        revalidate edits against the same numbers the plan was built from."""
        values = await self.case_values(case_id)
        if not values.get("incident") or not values.get("intelligence"):
            raise LookupError(f"case {case_id} has no confirmed incident and impact yet")
        incident = ConfirmedIncident.model_validate(values["incident"])
        return incident, OperationalIntelligenceResult.model_validate(values["intelligence"]).impact

    async def list_cases(self, authorization: AuthorizationContext) -> Sequence[CaseSummary]:
        case_ids = []
        async for checkpoint in self._checkpointer.alist(None):
            case_id = checkpoint.config["configurable"]["thread_id"]
            if case_id not in case_ids:
                case_ids.append(case_id)
        summaries = []
        for case_id in case_ids:
            state = await self._graph.aget_state(self._config(case_id))
            values = state.values
            if values.get("user_id") != authorization.user_id:
                continue
            incident = values.get("incident") or {}
            title = (
                (
                    f"{incident['supplier_name']}: {incident['component_id']}, "
                    f"{incident['delay_days']}-day delay"
                )
                if incident
                else "New case"
            )
            summaries.append(
                CaseSummary(
                    case_id=case_id,
                    user_id=values["user_id"],
                    title=title,
                    status=CaseStatus(values.get("status", CaseStatus.IN_PROGRESS.value)),
                    stage=state.next[0] if state.next else "done",
                    updated_at=datetime.fromisoformat(state.created_at),
                )
            )
        return sorted(summaries, key=lambda s: s.updated_at, reverse=True)
