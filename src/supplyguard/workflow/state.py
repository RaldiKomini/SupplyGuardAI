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
    investigation_findings: list[dict]  # InvestigationFinding from the Deep Agent
    plan: dict | None  # RecoveryPlan (Athanasia)
    approval_request: dict | None  # ApprovalRequest: the exact payload shown to the approver
    decision: dict | None  # the latest ApprovalDecision
    decision_problem: str | None  # why the latest decision was not accepted
    execution_results: list[dict] | None  # one ExecutionResult per action
    report: str | None  # the incident report (markdown)
    security_findings: Annotated[list[dict], operator.add]  # from the user's messages
    errors: Annotated[list[str], operator.add]  # problems met on the way; new ones are appended
