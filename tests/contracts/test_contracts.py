"""Smoke tests for strict shared contracts."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from supplyguard.contracts import (
    ApprovalRequest,
    ConfirmedIncident,
    ContractModel,
    InventoryPosition,
    RecoveryAction,
    RecoveryActionType,
    SourceReference,
)


def test_contract_models_are_strict_and_immutable() -> None:
    source = SourceReference(source_id="inventory.csv", source_type="structured")

    with pytest.raises(ValidationError):
        SourceReference(
            source_id="inventory.csv",
            source_type="structured",
            unexpected="forbidden",
        )

    with pytest.raises(ValidationError):
        source.source_id = "changed"  # type: ignore[misc]


def test_confirmed_incident_round_trips_as_json() -> None:
    incident = ConfirmedIncident(
        case_id="CASE-001",
        user_id="u-pm-01",
        supplier_id="S001",
        component_id="MC-17",
        delay_days=12,
        scenario_date=date(2026, 10, 19),
        spending_cap_eur=Decimal("75000"),
        original_message="Atlas announced a delay for MC-17.",
    )

    restored = ConfirmedIncident.model_validate_json(incident.model_dump_json())

    assert restored == incident


def test_approval_request_carries_exact_payload_identity() -> None:
    action = RecoveryAction(
        action_id="ACT-001",
        action_type=RecoveryActionType.PURCHASE_ALTERNATIVE,
        payload={"supplier_id": "S004", "component_id": "MC-17", "qty": 3000},
        cost_eur=Decimal("29400"),
        expected_completion_date=date(2026, 10, 28),
    )
    request = ApprovalRequest(
        approval_request_id="APR-001",
        case_id="CASE-001",
        option_id="OPT-001",
        payload_version=1,
        payload_hash="sha256:example",
        actions=(action,),
        total_cost_eur=Decimal("29400"),
        required_approver_roles=("procurement_manager", "finance_controller"),
        evidence_source_ids=("DOC-CON-S004-v1",),
        requested_at=datetime(2026, 10, 19, tzinfo=UTC),
    )

    assert request.payload_hash == "sha256:example"
    assert request.actions[0].payload["qty"] == 3000


def test_contract_base_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        ContractModel.model_validate({"unknown": True})


def test_inventory_contract_enforces_deterministic_usable_stock() -> None:
    with pytest.raises(ValidationError):
        InventoryPosition(
            component_id="MC-17",
            location_id="W-KAT",
            on_hand=1000,
            reserved=300,
            quarantined=100,
            usable=700,
        )
