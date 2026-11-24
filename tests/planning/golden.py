"""MC-17 golden scenario inputs, copied from support-data (golden_scenario.json and CSVs)."""

from datetime import date
from decimal import Decimal

from supplyguard.contracts import (
    ConfirmedIncident,
    CoveragePosition,
    ImpactReport,
    InventoryPosition,
    OpenPurchaseOrder,
    SupplierCapability,
)

SCENARIO_DATE = date(2026, 10, 19)

INCIDENT = ConfirmedIncident(
    case_id="CASE-GOLD",
    user_id="u-pm-01",
    supplier_id="S001",
    component_id="MC-17",
    delay_days=12,
    scenario_date=SCENARIO_DATE,
    spending_cap_eur=Decimal("75000"),
    original_message="Atlas Components announced a 12-day delay for MC-17.",
)


def _inventory(location: str, on_hand: int, reserved: int, quarantined: int) -> InventoryPosition:
    return InventoryPosition(
        component_id="MC-17",
        location_id=location,
        on_hand=on_hand,
        reserved=reserved,
        quarantined=quarantined,
        usable=on_hand - reserved - quarantined,
    )


def _coverage(location: str, factory: str | None, usable: int, cover: str) -> CoveragePosition:
    return CoveragePosition(
        factory_id=factory,
        location_id=location,
        usable_units=usable,
        daily_demand_units=Decimal("150") if factory else Decimal("450"),
        days_of_cover=Decimal(cover),
    )


def _supplier(
    supplier_id: str,
    price: str,
    lead_time: int,
    capacity: int,
    moq: int,
    upstream: str,
    certification: str = "valid",
) -> SupplierCapability:
    return SupplierCapability(
        supplier_id=supplier_id,
        component_id="MC-17",
        lead_time_days=lead_time,
        monthly_capacity_units=capacity,
        minimum_order_units=moq,
        unit_price_eur=Decimal(price),
        certification_status=certification,
        erp_status="approved",
        upstream_manufacturer_id=upstream,
    )


IMPACT = ImpactReport(
    case_id="CASE-GOLD",
    component_id="MC-17",
    inventory=(
        _inventory("W-STU", 1800, 500, 0),
        _inventory("W-KAT", 1500, 300, 600),
        _inventory("W-LYO", 1100, 600, 0),
        _inventory("W-HUB", 800, 0, 0),
    ),
    total_usable_units=3200,
    daily_demand_units=Decimal("450"),
    projected_shortfall_units=5350,
    affected_product_ids=("P012", "P023", "P031", "P047", "P058", "P066"),
    affected_factory_ids=("F1", "F2", "F3"),
    at_risk_orders=(),
    revenue_at_risk_eur=Decimal("37983380"),
    supplier_capabilities=(
        _supplier("S001", "8.5", 14, 9000, 1000, "U01"),
        _supplier("S002", "7.2", 21, 1500, 500, "U03"),
        _supplier("S003", "9.1", 6, 3500, 500, "U04", certification="expired"),
        _supplier("S004", "9.8", 9, 3000, 250, "U02"),
        _supplier("S005", "10.4", 8, 2500, 250, "U01"),
    ),
    open_purchase_orders=(
        OpenPurchaseOrder(
            po_id="PO-2026-9001",
            supplier_id="S001",
            component_id="MC-17",
            quantity=6000,
            unit_price_eur=Decimal("8.5"),
            promised_date=date(2026, 10, 26),
            status="open",
            expedite_allowed=False,
            expedite_fee_percentage=Decimal("0"),
            expedite_days_gain=0,
        ),
        OpenPurchaseOrder(
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
        ),
    ),
    coverage=(
        _coverage("W-STU", "F1", 1300, "8.7"),
        _coverage("W-KAT", "F2", 600, "4.0"),
        _coverage("W-LYO", "F3", 500, "3.3"),
        _coverage("W-HUB", None, 800, "1.8"),
    ),
)
