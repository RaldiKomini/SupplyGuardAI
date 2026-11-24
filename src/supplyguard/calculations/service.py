"""Public deterministic cost service used by recovery planning."""

from decimal import ROUND_CEILING, Decimal

from supplyguard.calculations.impact import estimate_late_penalty, expedite_cost
from supplyguard.contracts import (
    CalculationTrace,
    LatePenaltyCostInput,
    OpenPurchaseOrder,
    PurchaseCostInput,
    TransferCostInput,
)


class DeterministicCostService:
    """Implements the shared cost protocol without database or model dependencies."""

    def estimate_purchase(self, cost_input: PurchaseCostInput) -> CalculationTrace:
        return CalculationTrace(
            calculation="purchase_cost",
            formula="quantity * unit_price_eur",
            inputs={"quantity": cost_input.quantity, "unit_price_eur": cost_input.unit_price_eur},
            result=(cost_input.quantity * cost_input.unit_price_eur).quantize(Decimal("0.01")),
            unit="EUR",
            sources=cost_input.sources,
        )

    def estimate_transfer(self, cost_input: TransferCostInput) -> CalculationTrace:
        rate = cost_input.transfer_cost_eur_per_unit
        return CalculationTrace(
            calculation="transfer_cost",
            formula="quantity * transfer_cost_eur_per_unit",
            inputs={"quantity": cost_input.quantity, "transfer_cost_eur_per_unit": rate},
            result=(cost_input.quantity * rate).quantize(Decimal("0.01")),
            unit="EUR",
            sources=cost_input.sources,
        )

    def estimate_expedite(self, purchase_order: OpenPurchaseOrder) -> CalculationTrace:
        if not purchase_order.expedite_allowed:
            raise ValueError(f"purchase order {purchase_order.po_id} cannot be expedited")
        base_cost = Decimal(purchase_order.quantity) * purchase_order.unit_price_eur
        cost = expedite_cost(base_cost, purchase_order.expedite_fee_percentage)
        return CalculationTrace(
            calculation="expedite_cost",
            formula="quantity * unit_price_eur * (expedite_fee_percentage / 100)",
            inputs={
                "quantity": purchase_order.quantity,
                "unit_price_eur": purchase_order.unit_price_eur,
                "expedite_fee_percentage": purchase_order.expedite_fee_percentage,
            },
            result=cost,
            unit="EUR",
            sources=purchase_order.sources,
        )

    def estimate_late_penalty(self, cost_input: LatePenaltyCostInput) -> CalculationTrace:
        weeks = min(
            cost_input.cap_weeks,
            int((Decimal(cost_input.delay_days) / 7).to_integral_value(rounding=ROUND_CEILING)),
        )
        cost = estimate_late_penalty(
            cost_input.revenue_eur,
            cost_input.delay_days,
            cost_input.weekly_rate_percentage,
            cap_weeks=cost_input.cap_weeks,
        )
        return CalculationTrace(
            calculation="late_penalty_cost",
            formula="revenue_eur * (weekly_rate_percentage / 100) * capped_delay_weeks",
            inputs={
                "revenue_eur": cost_input.revenue_eur,
                "delay_days": cost_input.delay_days,
                "weekly_rate_percentage": cost_input.weekly_rate_percentage,
                "cap_weeks": cost_input.cap_weeks,
                "capped_delay_weeks": weeks,
            },
            result=cost,
            unit="EUR",
            sources=cost_input.sources,
        )
