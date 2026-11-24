"""Rule-based screening of alternative suppliers.

Every rule here is deterministic: the same inputs always give the same verdict,
and every rejection carries a human-readable reason for the approval screen.
"""

from dataclasses import dataclass

from supplyguard.contracts import SupplierCapability

VALID_CERTIFICATION = "valid"
APPROVED_ERP_STATUS = "approved"


@dataclass(frozen=True)
class SupplierScreening:
    """Verdict on one alternative supplier for the disrupted component."""

    supplier_id: str
    feasible: bool
    arrival_day: int
    max_units: int
    rejection_reasons: tuple[str, ...]
    warnings: tuple[str, ...]


def screen_supplier(
    capability: SupplierCapability,
    *,
    latest_useful_arrival_day: int,
    disrupted_upstream_id: str | None,
) -> SupplierScreening:
    """Decide whether a supplier can help before the delayed delivery arrives.

    latest_useful_arrival_day: days from the scenario date until the delayed
    delivery is expected (19 for MC-17). A supplier arriving on or after that
    day does not prevent the disruption.
    """
    reasons: list[str] = []
    warnings: list[str] = []

    if capability.certification_status != VALID_CERTIFICATION:
        reasons.append(
            f"certification is {capability.certification_status}; a valid certification is required"
        )
    if capability.erp_status != APPROVED_ERP_STATUS:
        reasons.append(f"ERP status is {capability.erp_status}, not approved")
    if capability.lead_time_days >= latest_useful_arrival_day:
        reasons.append(
            f"lead time {capability.lead_time_days} days: arrives on or after "
            f"day {latest_useful_arrival_day}, when the delayed delivery arrives"
        )
    if capability.monthly_capacity_units == 0:
        reasons.append("no available monthly capacity")
    if (
        disrupted_upstream_id is not None
        and capability.upstream_manufacturer_id == disrupted_upstream_id
    ):
        warnings.append(
            f"shares upstream manufacturer {disrupted_upstream_id} with the "
            "disrupted supplier: weak diversification"
        )

    return SupplierScreening(
        supplier_id=capability.supplier_id,
        feasible=not reasons,
        arrival_day=capability.lead_time_days,
        max_units=capability.monthly_capacity_units,
        rejection_reasons=tuple(reasons),
        warnings=tuple(warnings),
    )


def validate_order_quantity(capability: SupplierCapability, quantity: int) -> tuple[str, ...]:
    """Return the reasons a purchase quantity is not acceptable (empty if it is)."""
    reasons: list[str] = []
    if quantity <= 0:
        reasons.append("quantity must be positive")
    if quantity > capability.monthly_capacity_units:
        reasons.append(
            f"quantity {quantity} exceeds monthly capacity {capability.monthly_capacity_units}"
        )
    if 0 < quantity < capability.minimum_order_units:
        reasons.append(
            f"quantity {quantity} is below the minimum order of {capability.minimum_order_units}"
        )
    return tuple(reasons)
