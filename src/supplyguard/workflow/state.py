"""CaseState: what the workflow remembers about one case. One case is one LangGraph thread.

Contract models are stored as JSON dicts (model_dump(mode="json")) so checkpoints stay plain data.
"""

from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class CaseState(TypedDict, total=False):
    case_id: str
    user_id: str
    status: str  # a CaseStatus value
    messages: Annotated[list[AnyMessage], add_messages]  # new messages are appended
    incident: dict | None  # ConfirmedIncident, once intake is complete
    questions: list[str]  # what intake still needs to ask
    clarification_rounds: int
