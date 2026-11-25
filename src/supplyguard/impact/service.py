"""Coordinates repositories, deterministic calculations, graph, and risk evidence."""

import asyncio
import re
from collections.abc import Sequence
from datetime import date, timedelta
from decimal import Decimal

from pydantic import ValidationError

from supplyguard.calculations import (
    DeterministicCostService,
    days_of_cover,
    projected_stockout_date,
)
from supplyguard.calculations.impact import shortfall_from_demand
from supplyguard.contracts import (
    AtRiskOrder,
    CalculationTrace,
    ConfirmedIncident,
    CoveragePosition,
    DataQualityWarning,
    DependencyFinding,
    DependencyReport,
    ImpactReport,
    InventoryPosition,
    LatePenaltyCostInput,
    OpenPurchaseOrder,
    OperationalIntelligenceResult,
    Severity,
    SourceReference,
    SupplierCapability,
)
from supplyguard.contracts.protocols import OperationalCostService
from supplyguard.external_risk.service import ExternalRiskService
from supplyguard.graph.client import DependencyContext, DependencyGraph
from supplyguard.repositories.operational import (
    InventoryRow,
    OpenPurchaseOrderRow,
    OperationalRepository,
    WeeklyDemandRow,
)

PURCHASE_ORDER_ID = re.compile(r"\bPO-\d{4}-\d+\b", re.IGNORECASE)
WEEK = timedelta(days=7)


def csv_source(filename: str, record_id: str | None = None) -> SourceReference:
    return SourceReference(source_id=filename, source_type="csv", record_id=record_id)


def _delayed_order(
    incident: ConfirmedIncident, orders: Sequence[OpenPurchaseOrderRow]
) -> tuple[OpenPurchaseOrderRow | None, DataQualityWarning | None]:
    matching = [
        order
        for order in orders
        if order.supplier_id == incident.supplier_id and order.component_id == incident.component_id
    ]
    named_ids = {po.upper() for po in PURCHASE_ORDER_ID.findall(incident.original_message)}
    if len(named_ids) == 1:
        named_id = next(iter(named_ids))
        order = next((order for order in matching if order.po_id.upper() == named_id), None)
        record_id = named_id
        message = f"{named_id} is not a pending order for this supplier and component."
    elif named_ids:
        order = None
        record_id = incident.component_id
        message = "The incident names multiple purchase orders and the delayed order is unclear."
    else:
        order = min(
            (order for order in matching if order.promised_date >= incident.scenario_date),
            key=lambda order: (order.promised_date, order.po_id),
            default=None,
        )
        record_id = incident.component_id
        message = "No upcoming pending order matches the affected supplier and component."
    if order is not None:
        return order, None
    return None, DataQualityWarning(
        issue_type="delayed_order_unconfirmed",
        message=f"{message} The demand window is provisional and uses the stated delay only.",
        severity=Severity.WARNING,
        record_id=record_id,
        sources=(csv_source("purchase_orders.csv", record_id),),
    )


def _weighted_weekly_units(
    rows: Sequence[WeeklyDemandRow], start: date, end: date
) -> dict[str, int]:
    """Weight each weekly quantity by overlapping days before dividing by seven."""
    weighted: dict[str, int] = {}
    for row in rows:
        overlap = (min(end, row.week_start + WEEK) - max(start, row.week_start)).days
        if overlap > 0:
            weighted[row.factory_id] = (
                weighted.get(row.factory_id, 0) + row.component_units * overlap
            )
    return weighted


def _verified_inventory(
    rows: Sequence[InventoryRow],
) -> tuple[list[tuple[InventoryRow, InventoryPosition]], list[DataQualityWarning]]:
    verified: list[tuple[InventoryRow, InventoryPosition]] = []
    warnings: list[DataQualityWarning] = []
    for row in rows:
        record_id = f"{row.component_id}|{row.location_id}"
        sources = (csv_source("inventory.csv", record_id),)
        try:
            position = InventoryPosition(
                component_id=row.component_id,
                location_id=row.location_id,
                on_hand=row.on_hand,
                reserved=row.reserved,
                quarantined=row.quarantined,
                usable=row.usable,
                sources=sources,
            )
        except ValidationError:
            warnings.append(
                DataQualityWarning(
                    issue_type="inventory_total_unconfirmed",
                    message=(
                        f"Stock at {row.location_id} is inconsistent and was excluded. "
                        "The stock total is a verified subtotal and shortfall is provisional."
                    ),
                    severity=Severity.BLOCKING,
                    record_id=record_id,
                    sources=sources,
                )
            )
        else:
            verified.append((row, position))
    return verified, warnings


class DefaultOperationalIntelligenceService:
    """Public implementation of the shared OperationalIntelligenceService protocol."""

    def __init__(
        self,
        repository: OperationalRepository,
        graph: DependencyGraph,
        external_risk: ExternalRiskService,
        cost_service: OperationalCostService | None = None,
    ) -> None:
        self.repository = repository
        self.graph = graph
        self.external_risk = external_risk
        self.cost_service = cost_service or DeterministicCostService()

    async def investigate(self, incident: ConfirmedIncident) -> OperationalIntelligenceResult:
        # Graph I/O is independent; overlap it with sequential queries on one DB connection.
        async with asyncio.TaskGroup() as tasks:
            graph_task = tasks.create_task(
                self.graph.context_for(incident.supplier_id, incident.component_id)
            )
            inventory_rows = await self.repository.inventory_for_component(incident.component_id)
            product_ids = await self.repository.affected_product_ids(incident.component_id)
            orders = await self.repository.open_orders(product_ids, incident.scenario_date)
            capabilities = await self.repository.supplier_capabilities(incident.component_id)
            purchase_orders = await self.repository.open_purchase_orders(incident.component_id)
            delayed_order, delivery_warning = _delayed_order(incident, purchase_orders)
            original_arrival = (
                delayed_order.promised_date if delayed_order else incident.scenario_date
            )
            delayed_arrival = original_arrival + timedelta(days=incident.delay_days)
            demand_rows = await self.repository.weekly_component_demand(
                incident.component_id, incident.scenario_date, delayed_arrival
            )
            data_warnings = await self.repository.quality_warnings(
                (incident.supplier_id, incident.component_id, *product_ids)
            )
        graph_context = graph_task.result()

        risks, risk_warnings = await self.external_risk.evidence(
            upstream_manufacturer_ids=graph_context.upstream_manufacturer_ids,
            region=graph_context.upstream_regions[0] if graph_context.upstream_regions else None,
        )
        warnings = [*data_warnings, *risk_warnings]
        if delivery_warning is not None:
            warnings.append(delivery_warning)
        if set(product_ids) != set(graph_context.product_ids):
            warnings.append(
                DataQualityWarning(
                    issue_type="graph_relational_mismatch",
                    message=(
                        "Neo4j and PostgreSQL disagree on affected products; PostgreSQL was used."
                    ),
                    severity=Severity.WARNING,
                    record_id=incident.component_id,
                    sources=(
                        csv_source("bill_of_materials.csv", incident.component_id),
                        SourceReference(source_id="neo4j", source_type="graph"),
                    ),
                )
            )

        verified_inventory, inventory_warnings = _verified_inventory(inventory_rows)
        warnings.extend(inventory_warnings)
        total_usable = sum(position.usable for _, position in verified_inventory)
        window_days = max(0, (delayed_arrival - incident.scenario_date).days)
        weighted_units = _weighted_weekly_units(
            demand_rows, incident.scenario_date, delayed_arrival
        )
        demand_before_arrival = Decimal(sum(weighted_units.values())) / 7
        shortfall = shortfall_from_demand(total_usable, demand_before_arrival)
        average_divisor = 7 * max(1, window_days)
        daily_demand = Decimal(sum(weighted_units.values())) / average_divisor
        demand_by_factory = {
            factory: Decimal(units) / average_divisor for factory, units in weighted_units.items()
        }
        coverage = []
        for row, position in verified_inventory:
            local_demand = (
                demand_by_factory.get(row.factory_id, Decimal(0))
                if row.factory_id
                else daily_demand
            )
            coverage.append(
                CoveragePosition(
                    factory_id=row.factory_id,
                    location_id=row.location_id,
                    usable_units=position.usable,
                    daily_demand_units=local_demand,
                    days_of_cover=days_of_cover(position.usable, local_demand),
                    projected_stockout_date=projected_stockout_date(
                        incident.scenario_date, position.usable, local_demand
                    ),
                    sources=position.sources,
                )
            )
        at_risk_orders = tuple(
            AtRiskOrder(
                order_id=row.order_id,
                customer_id=row.customer_id,
                product_id=row.product_id,
                due_date=row.due_date,
                quantity=row.quantity,
                priority_tier=row.priority_tier,
                revenue_eur=row.revenue_eur,
                sources=(csv_source("customer_orders.csv", row.order_id),),
            )
            for row in orders
        )
        supplier_capabilities = tuple(
            SupplierCapability(
                supplier_id=row.supplier_id,
                component_id=row.component_id,
                lead_time_days=row.lead_time_days,
                monthly_capacity_units=row.monthly_capacity_units,
                minimum_order_units=row.minimum_order_units,
                unit_price_eur=row.unit_price_eur,
                certification_status=row.certification_status,
                erp_status=row.erp_status,
                upstream_manufacturer_id=row.upstream_manufacturer_id,
                sources=(
                    csv_source("supplier_components.csv", f"{row.supplier_id}|{row.component_id}"),
                ),
            )
            for row in capabilities
        )
        open_purchase_orders = tuple(
            OpenPurchaseOrder(
                po_id=row.po_id,
                supplier_id=row.supplier_id,
                component_id=row.component_id,
                quantity=row.quantity,
                unit_price_eur=row.unit_price_eur,
                promised_date=row.promised_date,
                status=row.status,
                expedite_allowed=row.expedite_allowed,
                expedite_fee_percentage=row.expedite_fee_percentage,
                expedite_days_gain=row.expedite_days_gain,
                sources=(csv_source("purchase_orders.csv", row.po_id),),
            )
            for row in purchase_orders
        )
        expedite_calculations = tuple(
            self.cost_service.estimate_expedite(purchase_order)
            for purchase_order in open_purchase_orders
            if purchase_order.expedite_allowed
        )
        penalty_calculations = tuple(
            self.cost_service.estimate_late_penalty(
                LatePenaltyCostInput(
                    revenue_eur=row.revenue_eur,
                    delay_days=(delayed_arrival - row.due_date).days,
                    weekly_rate_percentage=row.weekly_late_penalty_percentage,
                    sources=(csv_source("customer_orders.csv", row.order_id),),
                )
            )
            for row in {item.order_id: item for item in orders}.values()
            if delayed_order
            and row.priority_tier == 1
            and row.due_date < delayed_arrival
            and row.weekly_late_penalty_percentage > 0
        )
        calculations = (
            CalculationTrace(
                calculation="projected_shortfall",
                formula="ceil(max(0, demand_before_arrival - usable))",
                inputs={
                    "daily_demand": daily_demand,
                    "days_to_arrival": (original_arrival - incident.scenario_date).days,
                    "delay_days": incident.delay_days,
                    "window_days": window_days,
                    "demand_before_arrival": demand_before_arrival,
                    "usable": total_usable,
                    "excluded_inventory_locations": len(inventory_warnings),
                },
                result=shortfall,
                unit="units",
                sources=(
                    csv_source("inventory.csv", incident.component_id),
                    csv_source("production_schedule.csv", incident.component_id),
                    csv_source(
                        "purchase_orders.csv",
                        delayed_order.po_id if delayed_order else incident.component_id,
                    ),
                ),
            ),
            *expedite_calculations,
            *penalty_calculations,
        )
        impact = ImpactReport(
            case_id=incident.case_id,
            component_id=incident.component_id,
            inventory=tuple(position for _, position in verified_inventory),
            total_usable_units=total_usable,
            daily_demand_units=daily_demand,
            projected_shortfall_units=shortfall,
            first_stockout_date=(
                None
                if inventory_warnings
                else projected_stockout_date(incident.scenario_date, total_usable, daily_demand)
            ),
            affected_product_ids=product_ids,
            affected_factory_ids=graph_context.factory_ids,
            at_risk_orders=at_risk_orders,
            revenue_at_risk_eur=sum((row.revenue_eur for row in orders), start=Decimal(0)),
            supplier_capabilities=supplier_capabilities,
            open_purchase_orders=open_purchase_orders,
            coverage=tuple(coverage),
            calculations=calculations,
            warnings=tuple(warnings),
            sources=(csv_source("components.csv", incident.component_id),),
        )
        dependencies = self._dependency_report(incident.case_id, graph_context)
        return OperationalIntelligenceResult(
            impact=impact,
            dependencies=dependencies,
            external_risks=risks,
            warnings=tuple(warnings),
        )

    @staticmethod
    def _dependency_report(case_id: str, context: DependencyContext) -> DependencyReport:
        findings = (
            DependencyFinding(
                finding_type="impact_path",
                summary="Products, factories, and customers reachable from the component.",
                node_ids=tuple(
                    dict.fromkeys(
                        (*context.product_ids, *context.factory_ids, *context.customer_ids)
                    )
                ),
                relationship_types=("USED_IN", "PRODUCED_AT", "ORDERED_BY"),
                sources=(SourceReference(source_id="neo4j", source_type="graph"),),
            ),
            DependencyFinding(
                finding_type="supplier_dependency",
                summary="Upstream manufacturers and alternative suppliers.",
                node_ids=tuple(
                    dict.fromkeys(
                        (
                            *context.upstream_manufacturer_ids,
                            *context.alternative_supplier_ids,
                        )
                    )
                ),
                relationship_types=("SOURCES_FROM", "SUPPLIES"),
                sources=(SourceReference(source_id="neo4j", source_type="graph"),),
            ),
        )
        if context.shared_upstream_supplier_ids:
            findings += (
                DependencyFinding(
                    finding_type="shared_upstream",
                    summary="These alternatives share an upstream manufacturer with the supplier.",
                    node_ids=context.shared_upstream_supplier_ids,
                    relationship_types=("SOURCES_FROM", "SUPPLIES"),
                    sources=(
                        csv_source("suppliers.csv"),
                        csv_source("supplier_components.csv"),
                    ),
                ),
            )
        return DependencyReport(
            case_id=case_id,
            findings=findings,
            disconnected_entity_ids=context.disconnected_entity_ids,
        )
