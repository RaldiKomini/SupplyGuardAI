"""Supplier screening against the MC-17 golden scenario (support-data)."""

from decimal import Decimal

import pytest

from supplyguard.contracts import SupplierCapability
from supplyguard.planning.screening import screen_supplier, validate_order_quantity

# Delayed Atlas delivery arrives on day 19 after the scenario date (2026-10-19).
LATEST_USEFUL_DAY = 19
ATLAS_UPSTREAM = "U01"  # Veltro Semiconductor


def _capability(
    supplier_id: str,
    *,
    price: str,
    lead_time: int,
    capacity: int,
    moq: int,
    certification: str = "valid",
    upstream: str,
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


# Values from structured/supplier_components.csv and structured/suppliers.csv.
HELIOS = _capability("S002", price="7.2", lead_time=21, capacity=1500, moq=500, upstream="U03")
BOREALIS = _capability(
    "S003",
    price="9.1",
    lead_time=6,
    capacity=3500,
    moq=500,
    certification="expired",
    upstream="U04",
)
CORDILLERA = _capability("S004", price="9.8", lead_time=9, capacity=3000, moq=250, upstream="U02")
DELTA = _capability("S005", price="10.4", lead_time=8, capacity=2500, moq=250, upstream="U01")


def _screen(capability: SupplierCapability):
    return screen_supplier(
        capability,
        latest_useful_arrival_day=LATEST_USEFUL_DAY,
        disrupted_upstream_id=ATLAS_UPSTREAM,
    )


def test_borealis_is_rejected_for_expired_certification() -> None:
    result = _screen(BOREALIS)

    assert not result.feasible
    assert any("certification is expired" in reason for reason in result.rejection_reasons)


def test_helios_is_rejected_as_too_slow() -> None:
    result = _screen(HELIOS)

    assert not result.feasible
    assert any("lead time 21 days" in reason for reason in result.rejection_reasons)


def test_cordillera_is_feasible_without_warnings() -> None:
    result = _screen(CORDILLERA)

    assert result.feasible
    assert result.warnings == ()
    assert result.max_units == 3000
    assert result.arrival_day == 9


def test_delta_is_feasible_but_flagged_for_shared_upstream() -> None:
    result = _screen(DELTA)

    assert result.feasible
    assert any("U01" in warning for warning in result.warnings)


@pytest.mark.parametrize(
    ("quantity", "expected_fragment"),
    [(9000, "exceeds monthly capacity"), (100, "below the minimum order"), (0, "positive")],
)
def test_invalid_cordillera_quantities_are_rejected(quantity: int, expected_fragment: str) -> None:
    reasons = validate_order_quantity(CORDILLERA, quantity)

    assert any(expected_fragment in reason for reason in reasons)


def test_cordillera_reference_quantity_is_accepted() -> None:
    assert validate_order_quantity(CORDILLERA, 3000) == ()
