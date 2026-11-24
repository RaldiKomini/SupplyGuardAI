"""Operational intelligence contracts."""

from datetime import date
from decimal import Decimal

from pydantic import AwareDatetime, Field, model_validator

from supplyguard.contracts.common import ContractModel, DataQualityWarning, SourceReference


class InventoryPosition(ContractModel):
    component_id: str
    location_id: str
    on_hand: int = Field(ge=0)
    reserved: int = Field(ge=0)
    quarantined: int = Field(ge=0)
    usable: int = Field(ge=0)
    sources: tuple[SourceReference, ...] = ()

    @model_validator(mode="after")
    def usable_matches_inventory_state(self) -> "InventoryPosition":
        expected = self.on_hand - self.reserved - self.quarantined
        if expected < 0:
            raise ValueError("reserved and quarantined stock exceed on-hand stock")
        if self.usable != expected:
            raise ValueError("usable must equal on_hand - reserved - quarantined")
        return self


class AtRiskOrder(ContractModel):
    order_id: str
    customer_id: str
    product_id: str
    due_date: date
    quantity: int = Field(ge=0)
    priority_tier: int = Field(ge=1)
    revenue_eur: Decimal = Field(ge=0)
    sources: tuple[SourceReference, ...] = ()


class SupplierCapability(ContractModel):
    supplier_id: str
    component_id: str
    lead_time_days: int = Field(ge=0)
    monthly_capacity_units: int = Field(ge=0)
    minimum_order_units: int = Field(ge=0)
    unit_price_eur: Decimal = Field(ge=0)
    certification_status: str
    erp_status: str
    upstream_manufacturer_id: str | None = None
    sources: tuple[SourceReference, ...] = ()


class OpenPurchaseOrder(ContractModel):
    po_id: str
    supplier_id: str
    component_id: str
    quantity: int = Field(gt=0)
    unit_price_eur: Decimal = Field(ge=0)
    promised_date: date
    status: str
    expedite_allowed: bool
    expedite_fee_percentage: Decimal = Field(ge=0, le=100)
    expedite_days_gain: int = Field(ge=0)
    sources: tuple[SourceReference, ...] = ()


class PurchaseCostInput(ContractModel):
    """Proposed purchase quantity and the unit price selected for the estimate."""

    quantity: int = Field(ge=0, strict=True)
    unit_price_eur: Decimal = Field(ge=0, allow_inf_nan=False)
    sources: tuple[SourceReference, ...] = ()


class TransferCostInput(ContractModel):
    """Proposed transfer quantity and the rate from trusted policy."""

    quantity: int = Field(ge=0, strict=True)
    transfer_cost_eur_per_unit: Decimal = Field(ge=0, allow_inf_nan=False)
    sources: tuple[SourceReference, ...] = ()


class LatePenaltyCostInput(ContractModel):
    revenue_eur: Decimal = Field(ge=0)
    delay_days: int = Field(ge=0)
    weekly_rate_percentage: Decimal = Field(ge=0, le=100)
    cap_weeks: int = Field(default=5, ge=0)
    sources: tuple[SourceReference, ...] = ()


class CoveragePosition(ContractModel):
    factory_id: str | None = None
    location_id: str
    usable_units: int = Field(ge=0)
    daily_demand_units: Decimal = Field(ge=0)
    days_of_cover: Decimal | None = Field(default=None, ge=0)
    projected_stockout_date: date | None = None
    sources: tuple[SourceReference, ...] = ()


class CalculationTrace(ContractModel):
    calculation: str
    formula: str
    inputs: dict[str, Decimal | int]
    result: Decimal | int
    unit: str
    sources: tuple[SourceReference, ...] = ()


class ImpactReport(ContractModel):
    case_id: str
    component_id: str
    inventory: tuple[InventoryPosition, ...]
    total_usable_units: int = Field(ge=0)
    daily_demand_units: Decimal = Field(ge=0)
    projected_shortfall_units: int = Field(ge=0)
    first_stockout_date: date | None = None
    affected_product_ids: tuple[str, ...]
    affected_factory_ids: tuple[str, ...]
    at_risk_orders: tuple[AtRiskOrder, ...]
    revenue_at_risk_eur: Decimal = Field(ge=0)
    supplier_capabilities: tuple[SupplierCapability, ...] = ()
    open_purchase_orders: tuple[OpenPurchaseOrder, ...] = ()
    coverage: tuple[CoveragePosition, ...] = ()
    calculations: tuple[CalculationTrace, ...] = ()
    warnings: tuple[DataQualityWarning, ...] = ()
    sources: tuple[SourceReference, ...] = ()


class DependencyFinding(ContractModel):
    finding_type: str
    summary: str
    node_ids: tuple[str, ...]
    relationship_types: tuple[str, ...]
    sources: tuple[SourceReference, ...] = ()


class DependencyReport(ContractModel):
    case_id: str
    findings: tuple[DependencyFinding, ...]
    disconnected_entity_ids: tuple[str, ...] = ()
    warnings: tuple[DataQualityWarning, ...] = ()


class RiskEvidence(ContractModel):
    provider: str
    upstream_manufacturer_id: str | None = None
    region: str | None = None
    topic: str
    severity: str
    summary: str
    alert_date: date | None = None
    retrieved_at: AwareDatetime
    available: bool = True
    sources: tuple[SourceReference, ...] = ()


class OperationalIntelligenceResult(ContractModel):
    impact: ImpactReport
    dependencies: DependencyReport
    external_risks: tuple[RiskEvidence, ...] = ()
    warnings: tuple[DataQualityWarning, ...] = ()
