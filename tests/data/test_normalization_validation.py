from datetime import date
from decimal import Decimal

from supplyguard.contracts import Severity
from supplyguard.data.normalization import normalize_row
from supplyguard.data.schema import inventory, supplier_components
from supplyguard.data.validation import stale_inventory_warning, validate_inventory_row


def test_normalize_row_uses_declared_types() -> None:
    row = normalize_row(
        {
            "supplier_id": " SUP-1 ",
            "component_id": "MC-17",
            "unit_price_eur": "12.25",
            "lead_time_days": "7",
            "monthly_capacity_units": "3000",
            "moq_units": "100",
            "approved_source": "yes",
            "ignored": "value",
        },
        supplier_components,
    )

    assert row["supplier_id"] == "SUP-1"
    assert row["unit_price_eur"] == Decimal("12.25")
    assert row["approved_source"] is True
    assert "ignored" not in row


def test_inventory_mismatch_is_blocking() -> None:
    warnings = validate_inventory_row(
        {
            "component_id": "MC-17",
            "on_hand": "10",
            "reserved": "2",
            "quarantined": "1",
            "usable": "8",
        }
    )

    assert warnings[0].issue_type == "inventory_balance_mismatch"
    assert warnings[0].severity is Severity.BLOCKING


def test_stale_inventory_is_warning_not_blocking() -> None:
    warning = stale_inventory_warning(
        "MC-17", date(2025, 1, 1), date(2025, 2, 2), stale_after_days=30
    )

    assert warning is not None
    assert warning.severity is Severity.WARNING


def test_inventory_schema_normalizes_dates_and_numbers() -> None:
    row = normalize_row(
        {
            "component_id": "MC-17",
            "location_id": "L1",
            "on_hand": "5",
            "reserved": "1",
            "quarantined": "0",
            "usable": "4",
            "last_counted_date": "2025-01-03",
        },
        inventory,
    )
    assert row["last_counted_date"] == date(2025, 1, 3)
