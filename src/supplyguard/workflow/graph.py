"""The case workflow graph.

intake -> investigate_impact -> collect_evidence -> plan_recovery -> request_approval
-> await_approval (pause) -> record_decision -> execute (approved) -> finish
                                             -> revalidate (modified) -> await_approval
                                             -> finish (rejected)

Every finished step is saved by the checkpointer, so after a restart the case continues at
the next unfinished step and finished steps (such as execution) are not repeated.
"""

from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from supplyguard.workflow.approval import (
    finish,
    make_approval_steps,
    route_after_await,
    route_after_record,
)
from supplyguard.workflow.intake.nodes import (
    ask_clarification,
    make_read_incident,
    route_after_read,
    stop_intake,
)
from supplyguard.workflow.investigation import (
    make_investigation_steps,
    route_after_impact,
    route_after_plan,
    stop_case,
)
from supplyguard.workflow.services import WorkflowServices
from supplyguard.workflow.state import CaseState


def build_graph(
    *, model: BaseChatModel, services: WorkflowServices, checkpointer: BaseCheckpointSaver
):
    """The checkpointer is required: pauses (questions, approval) are saved there, and the
    answer or a restart resumes from it."""
    if checkpointer is None:
        raise ValueError("build_graph needs a checkpointer to pause and resume cases")
    graph = StateGraph(CaseState)

    # Intake
    graph.add_node("read_incident", make_read_incident(model, services.master_data))
    graph.add_node("ask_clarification", ask_clarification)
    graph.add_node("stop_intake", stop_intake)
    graph.add_edge(START, "read_incident")
    graph.add_conditional_edges(
        "read_incident",
        route_after_read,
        {
            "confirmed": "investigate_impact",
            "ask_clarification": "ask_clarification",
            "stop_intake": "stop_intake",
        },
    )
    graph.add_edge("ask_clarification", "read_incident")  # read again, with the answer
    graph.add_edge("stop_intake", END)

    # Investigation
    for name, step in make_investigation_steps(services).items():
        graph.add_node(name, step)
    graph.add_node("stop_case", stop_case)
    graph.add_conditional_edges(
        "investigate_impact",
        route_after_impact,
        {"collect_evidence": "collect_evidence", "stop_case": "stop_case"},
    )
    graph.add_edge("collect_evidence", "plan_recovery")
    graph.add_conditional_edges(
        "plan_recovery",
        route_after_plan,
        {"ready_for_approval": "request_approval", "stop_case": "stop_case"},
    )
    graph.add_edge("stop_case", END)

    # Approval and execution
    for name, step in make_approval_steps(services).items():
        graph.add_node(name, step)
    graph.add_node("finish", finish)
    graph.add_edge("request_approval", "await_approval")
    graph.add_conditional_edges(
        "await_approval",
        route_after_await,
        {"await_approval": "await_approval", "record_decision": "record_decision"},
    )
    graph.add_conditional_edges(
        "record_decision",
        route_after_record,
        {
            "await_approval": "await_approval",
            "revalidate": "revalidate",
            "execute": "execute",
            "finish": "finish",
        },
    )
    graph.add_edge("revalidate", "await_approval")
    graph.add_edge("execute", "finish")
    graph.add_edge("finish", END)
    return graph.compile(checkpointer=checkpointer)
