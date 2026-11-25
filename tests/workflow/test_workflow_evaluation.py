"""The workflow's evaluators for the harness: ambiguity, follow-up, restart/recovery."""

import asyncio

import pytest

from supplyguard.contracts import AuthorizationContext
from supplyguard.evaluation.cases import EvaluationCase, EvaluationCategory
from supplyguard.workflow.evaluation import (
    AmbiguityEvaluator,
    FollowUpEvaluator,
    RestartEvaluator,
)
from supplyguard.workflow.follow_up import FollowUpIntent
from supplyguard.workflow.intake.extraction import IncidentExtraction
from supplyguard.workflow.service import CaseWorkflow

OWNER = AuthorizationContext(
    user_id="u-pm-01", role="procurement_manager", allowed_scopes=("internal", "procurement")
)
GOLDEN = IncidentExtraction(
    supplier_query="Atlas Components",
    component_query="MC-17",
    delay_days=12,
    spending_cap_eur=75000,
)


def case(category: EvaluationCategory, message: str) -> EvaluationCase:
    return EvaluationCase(id="EV-TEST", category=category, message=message, depends_on=None)


def evaluate(evaluator, evaluation_case):
    return asyncio.run(evaluator.evaluate(evaluation_case))


def test_ambiguous_supplier_passes_because_intake_asks(scripted_model, master_data):
    model = scripted_model(
        IncidentExtraction(supplier_query="Atlas", component_query="MC-140", delay_days=10)
    )

    result = evaluate(
        AmbiguityEvaluator(model, master_data),
        case(EvaluationCategory.AMBIGUITY, "Atlas called, MC-140 will be 10 days late."),
    )

    assert result.status == "passed"
    assert result.metrics["questions_asked"] >= 1


def test_clear_message_fails_the_ambiguity_check(scripted_model, master_data):
    result = evaluate(
        AmbiguityEvaluator(scripted_model(GOLDEN), master_data),
        case(EvaluationCategory.AMBIGUITY, "Atlas Components, MC-17, 12 days."),
    )

    assert result.status == "failed" and "without asking" in result.findings[0]


def test_follow_up_is_answered_without_changing_the_case(scripted_model, make_services):
    services = make_services()
    model = scripted_model(
        GOLDEN, FollowUpIntent(kind="change_spending_cap", new_spending_cap_eur=90000)
    )
    evaluator = FollowUpEvaluator(
        lambda saver: CaseWorkflow(model=model, services=services, checkpointer=saver), OWNER, ()
    )

    result = evaluate(
        evaluator,
        case(
            EvaluationCategory.FOLLOW_UP,
            "What changes if we increase the spending limit to EUR 90,000?",
        ),
    )

    assert result.status == "passed", result.findings
    assert services.execution.writes == []


@pytest.mark.parametrize("step", ["approval_pending", "after_graph_query", "before_execute"])
def test_restart_at_each_step_finishes_once_with_the_same_payload(
    step, scripted_model, make_services
):
    services = make_services()
    model = scripted_model(GOLDEN)  # one extraction: the restart must not read the case again
    evaluator = RestartEvaluator(
        lambda saver: CaseWorkflow(model=model, services=services, checkpointer=saver), OWNER, ()
    )

    result = evaluate(
        evaluator,
        case(
            EvaluationCategory.RESTART_RECOVERY,
            f"[harness] kill application at step '{step}', restart, resume case",
        ),
    )

    assert result.status == "passed", result.findings
    assert len(services.execution.writes) == 1  # executed once
    assert services.intelligence.calls == 1  # finished steps were not repeated
