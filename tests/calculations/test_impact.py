from datetime import date
from decimal import Decimal

import pytest

from supplyguard.calculations.impact import (
    days_of_cover,
    estimate_late_penalty,
    expedite_cost,
    projected_shortfall,
    projected_stockout_date,
    usable_inventory,
)


def test_golden_mc17_inventory_and_shortfall() -> None:
    usable = usable_inventory(on_hand=5200, reserved=1400, quarantined=600)

    assert usable == 3200
    assert days_of_cover(usable, Decimal(450)) == Decimal("7.1")
    assert projected_shortfall(usable, Decimal(450), 7, 12) == 5350
    assert projected_stockout_date(date(2025, 1, 1), usable, Decimal(450)) == date(2025, 1, 9)


def test_zero_demand_never_claims_a_stockout() -> None:
    assert days_of_cover(100, Decimal(0)) is None
    assert projected_stockout_date(date(2025, 1, 1), 100, Decimal(0)) is None


def test_invalid_inventory_is_rejected() -> None:
    with pytest.raises(ValueError, match="exceed"):
        usable_inventory(10, 8, 3)


def test_cost_and_penalty_rounding() -> None:
    assert estimate_late_penalty(Decimal("1000"), 8, Decimal("2")) == Decimal("40.00")
    assert estimate_late_penalty(Decimal("1000"), 15, Decimal("2")) == Decimal("50.00")
    assert estimate_late_penalty(Decimal("1000"), 100, Decimal("0.25")) == Decimal("37.50")
    assert expedite_cost(Decimal("123.45"), Decimal("7.5")) == Decimal("9.26")
