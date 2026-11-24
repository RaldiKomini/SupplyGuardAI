"""The case workflow graph. For now only intake; later steps are added after "confirmed"."""

from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from supplyguard.contracts.protocols import MasterDataService
from supplyguard.workflow.intake.nodes import (
    ask_clarification,
    make_read_incident,
    route_after_read,
    stop_intake,
)
from supplyguard.workflow.state import CaseState


def build_graph(
    *, model: BaseChatModel, master_data: MasterDataService, checkpointer: BaseCheckpointSaver
):
    """The checkpointer is required: pausing for a question (interrupt) saves the case there,
    and the answer resumes from it."""
    if checkpointer is None:
        raise ValueError("build_graph needs a checkpointer to pause and resume cases")
    graph = StateGraph(CaseState)
    graph.add_node("read_incident", make_read_incident(model, master_data))
    graph.add_node("ask_clarification", ask_clarification)
    graph.add_node("stop_intake", stop_intake)

    graph.add_edge(START, "read_incident")
    graph.add_conditional_edges(
        "read_incident",
        route_after_read,
        {"confirmed": END, "ask_clarification": "ask_clarification", "stop_intake": "stop_intake"},
    )
    graph.add_edge("ask_clarification", "read_incident")  # read again, with the answer
    graph.add_edge("stop_intake", END)
    return graph.compile(checkpointer=checkpointer)
