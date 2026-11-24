import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from supplyguard.calculations import DeterministicCostService
from supplyguard.contracts import (
    LatePenaltyCostInput,
    OpenPurchaseOrder,
    PurchaseCostInput,
    SourceReference,
    TransferCostInput,
)
from supplyguard.contracts.protocols import OperationalCostService


def test_mc17_purchase_and_transfer_costs() -> None:
    costs: OperationalCostService = DeterministicCostService()
    purchase_source = SourceReference(
        source_id="supplier_components.csv", source_type="csv", record_id="S004|MC-17"
    )
    policy_source = SourceReference(
        source_id="support-data/config/policies.json",
        source_type="policy",
        record_id="transfer_cost_eur_per_unit",
    )
    policy_path = Path(__file__).resolve().parents[2] / policy_source.source_id
    policy = json.loads(policy_path.read_text(encoding="utf-8"), parse_float=Decimal)

    purchase = costs.estimate_purchase(
        PurchaseCostInput(quantity=3000, unit_price_eur=Decimal("9.80"), sources=(purchase_source,))
    )
    transfer = costs.estimate_transfer(
        TransferCostInput(
            quantity=800,
            transfer_cost_eur_per_unit=policy["transfer_cost_eur_per_unit"],
            sources=(policy_source,),
        )
    )

    assert purchase.result == Decimal("29400.00")
    assert purchase.sources == (purchase_source,)
    assert transfer.result == Decimal("96.00")
    assert transfer.sources == (policy_source,)
    assert transfer.inputs == {"quantity": 800, "transfer_cost_eur_per_unit": Decimal("0.12")}
    assert purchase.unit == transfer.unit == "EUR"


def test_purchase_rounds_total_to_cents_and_zero_transfer_costs_nothing() -> None:
    costs = DeterministicCostService()
    purchase = costs.estimate_purchase(
        PurchaseCostInput(quantity=3, unit_price_eur=Decimal("0.1234"))
    )
    transfer = costs.estimate_transfer(
        TransferCostInput(quantity=0, transfer_cost_eur_per_unit=Decimal("0.12"))
    )

    assert purchase.result == Decimal("0.37")
    assert transfer.result == Decimal("0.00")


def test_po_2026_9002_expedite_cost_is_882_eur() -> None:
    purchase_order = OpenPurchaseOrder(
        po_id="PO-2026-9002",
        supplier_id="S004",
        component_id="MC-17",
        quantity=1500,
        unit_price_eur=Decimal("9.8"),
        promised_date=date(2026, 11, 2),
        status="open",
        expedite_allowed=True,
        expedite_fee_percentage=Decimal("6"),
        expedite_days_gain=7,
    )

    trace = DeterministicCostService().estimate_expedite(purchase_order)

    assert trace.result == Decimal("882.00")
    assert trace.inputs["expedite_fee_percentage"] == Decimal("6")


def test_non_expeditable_order_is_rejected() -> None:
    purchase_order = OpenPurchaseOrder(
        po_id="PO-1",
        supplier_id="S001",
        component_id="MC-17",
        quantity=100,
        unit_price_eur=Decimal("10"),
        promised_date=date(2026, 11, 2),
        status="open",
        expedite_allowed=False,
        expedite_fee_percentage=Decimal("0"),
        expedite_days_gain=0,
    )

    with pytest.raises(ValueError, match="cannot be expedited"):
        DeterministicCostService().estimate_expedite(purchase_order)


def test_late_penalty_service_returns_trace() -> None:
    trace = DeterministicCostService().estimate_late_penalty(
        LatePenaltyCostInput(
            revenue_eur=Decimal("1000"),
            delay_days=8,
            weekly_rate_percentage=Decimal("2"),
        )
    )

    assert trace.result == Decimal("40.00")
    assert trace.inputs["capped_delay_weeks"] == 2
