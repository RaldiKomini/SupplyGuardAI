from datetime import date
from decimal import Decimal

import pytest

from supplyguard.calculations import DeterministicCostService
from supplyguard.contracts import LatePenaltyCostInput, OpenPurchaseOrder


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
