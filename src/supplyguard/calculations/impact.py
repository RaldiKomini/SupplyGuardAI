"""Authoritative arithmetic kept independent from databases and LLMs."""

from datetime import date, timedelta
from decimal import ROUND_CEILING, Decimal


def usable_inventory(on_hand: int, reserved: int, quarantined: int) -> int:
    result = on_hand - reserved - quarantined
    if result < 0:
        raise ValueError("reserved and quarantined stock exceed on-hand stock")
    return result


def days_of_cover(usable_units: int, daily_demand_units: Decimal) -> Decimal | None:
    if usable_units < 0 or daily_demand_units < 0:
        raise ValueError("inventory and demand cannot be negative")
    if daily_demand_units == 0:
        return None
    return (Decimal(usable_units) / daily_demand_units).quantize(Decimal("0.1"))


def projected_shortfall(
    usable_units: int,
    daily_demand_units: Decimal,
    days_until_original_arrival: int,
    delay_days: int,
) -> int:
    """Units missing before the now-delayed replenishment arrives."""
    if min(usable_units, days_until_original_arrival, delay_days) < 0 or daily_demand_units < 0:
        raise ValueError("shortfall inputs cannot be negative")
    demand_until_arrival = daily_demand_units * (days_until_original_arrival + delay_days)
    missing = max(Decimal(0), demand_until_arrival - usable_units)
    return int(missing.to_integral_value(rounding=ROUND_CEILING))


def projected_stockout_date(
    scenario_date: date, usable_units: int, daily_demand_units: Decimal
) -> date | None:
    if daily_demand_units <= 0:
        return None
    days = (Decimal(usable_units) / daily_demand_units).to_integral_value(rounding=ROUND_CEILING)
    return scenario_date + timedelta(days=int(days))


def estimate_late_penalty(
    revenue_eur: Decimal,
    delay_days: int,
    weekly_rate_percentage: Decimal,
    *,
    cap_weeks: int = 5,
) -> Decimal:
    """Apply a weekly order penalty, charging any partial week as a full week."""
    if min(revenue_eur, weekly_rate_percentage) < 0 or delay_days < 0:
        raise ValueError("penalty inputs cannot be negative")
    weeks = min(cap_weeks, int((Decimal(delay_days) / 7).to_integral_value(rounding=ROUND_CEILING)))
    return (revenue_eur * (weekly_rate_percentage / 100) * weeks).quantize(Decimal("0.01"))


def expedite_cost(base_cost_eur: Decimal, fee_percentage: Decimal) -> Decimal:
    if min(base_cost_eur, fee_percentage) < 0:
        raise ValueError("cost inputs cannot be negative")
    return (base_cost_eur * (fee_percentage / 100)).quantize(Decimal("0.01"))
