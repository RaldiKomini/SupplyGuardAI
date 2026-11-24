"""Tests for the authorization and case-listing contracts."""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from supplyguard.contracts import (
    AuthorizationContext,
    CaseStatus,
    CaseSummary,
    UIEvent,
    UIEventType,
)
from supplyguard.contracts.protocols import CaseWorkflowService


def test_authorization_context_normalises_scopes_and_factories() -> None:
    context = AuthorizationContext(
        user_id="u-pm-01",
        role="procurement_manager",
        factory_ids=(" F-KAT ", "F-GDA", "F-KAT"),
        allowed_scopes=("public", " contracts ", "public"),
    )

    assert context.allowed_scopes == ("contracts", "public")
    assert context.factory_ids == ("F-GDA", "F-KAT")


def test_authorization_context_defaults_to_no_access() -> None:
    context = AuthorizationContext(user_id="u-01", role="viewer")

    assert context.allowed_scopes == ()
    assert context.factory_ids == ()


@pytest.mark.parametrize("bad_scopes", [("",), ("   ",)])
def test_authorization_context_rejects_blank_scopes(bad_scopes: tuple[str, ...]) -> None:
    with pytest.raises(ValidationError):
        AuthorizationContext(user_id="u-01", role="viewer", allowed_scopes=bad_scopes)


def test_authorization_context_is_immutable_and_strict() -> None:
    context = AuthorizationContext(user_id="u-01", role="viewer")

    with pytest.raises(ValidationError):
        context.role = "admin"  # type: ignore[misc]

    with pytest.raises(ValidationError):
        AuthorizationContext(user_id="u-01", role="viewer", is_admin=True)  # type: ignore[call-arg]


def test_authorization_context_round_trips_as_json() -> None:
    context = AuthorizationContext(user_id="u-01", role="viewer", allowed_scopes=("public",))

    assert AuthorizationContext.model_validate_json(context.model_dump_json()) == context


def test_case_summary_requires_timezone_aware_timestamp() -> None:
    with pytest.raises(ValidationError):
        CaseSummary(
            case_id="CASE-001",
            user_id="u-01",
            title="MC-17 delay",
            status=CaseStatus.IN_PROGRESS,
            stage="impact",
            updated_at=datetime(2026, 10, 19),
        )


def test_workflow_protocol_accepts_async_generator_implementations() -> None:
    class FakeWorkflow:
        async def resume_case(
            self, case_id: str, authorization: AuthorizationContext
        ) -> AsyncIterator[UIEvent]:
            yield UIEvent(
                case_id=case_id,
                event_type=UIEventType.PROGRESS,
                stage="impact",
                message="resumed",
                occurred_at=datetime(2026, 10, 19, tzinfo=UTC),
            )

    workflow: CaseWorkflowService = FakeWorkflow()  # type: ignore[assignment]
    context = AuthorizationContext(user_id="u-01", role="viewer")

    async def collect() -> list[UIEvent]:
        return [event async for event in workflow.resume_case("CASE-001", context)]

    events = asyncio.run(collect())

    assert [event.message for event in events] == ["resumed"]
