"""CaseState: what the workflow remembers about one case. One case is one LangGraph thread.

Contract models are stored as JSON dicts (model_dump(mode="json")) so checkpoints stay plain data.
"""

import operator
from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class CaseState(TypedDict, total=False):
    case_id: str
    user_id: str
    authorization: dict  # AuthorizationContext from the app, passed on unchanged
    status: str  # a CaseStatus value
    messages: Annotated[list[AnyMessage], add_messages]  # new messages are appended
    incident: dict | None  # ConfirmedIncident, once intake is complete
    questions: list[str]  # what intake still needs to ask
    clarification_rounds: int
    intelligence: dict | None  # OperationalIntelligenceResult (Raldi)
    evidence: dict | None  # EvidenceBundle (George)
    plan: dict | None  # RecoveryPlan (Athanasia)
    approval_request: dict | None  # ApprovalRequest: the exact payload shown to the approver
    revalidated_request: dict | None  # Proposed revision until its modification is recorded
    decision: dict | None  # the latest ApprovalDecision
    decision_problem: str | None  # why the latest decision was not accepted
    execution_results: list[dict] | None  # one ExecutionResult per action
    errors: Annotated[list[str], operator.add]  # problems met on the way; new ones are appended
