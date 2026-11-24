"""Coordinates repositories, deterministic calculations, graph, and risk evidence."""

import asyncio
from decimal import Decimal

from supplyguard.calculations import (
    DeterministicCostService,
    days_of_cover,
    projected_shortfall,
    projected_stockout_date,
)
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
    OpenPurchaseOrder,
    OperationalIntelligenceResult,
    Severity,
    SourceReference,
    SupplierCapability,
)
from supplyguard.contracts.protocols import OperationalCostService
from supplyguard.external_risk.service import ExternalRiskService
from supplyguard.graph.client import DependencyContext, DependencyGraph
from supplyguard.repositories.operational import OperationalRepository


def csv_source(filename: str, record_id: str | None = None) -> SourceReference:
    return SourceReference(source_id=filename, source_type="csv", record_id=record_id)


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
            daily_demand = await self.repository.average_daily_component_demand(
                incident.component_id, incident.scenario_date
            )
            factory_demand = await self.repository.daily_demand_by_factory(
                incident.component_id, incident.scenario_date
            )
            orders = await self.repository.open_orders(product_ids, incident.scenario_date)
            capabilities = await self.repository.supplier_capabilities(incident.component_id)
            purchase_orders = await self.repository.open_purchase_orders(incident.component_id)
            data_warnings = await self.repository.quality_warnings(
                (incident.supplier_id, incident.component_id, *product_ids)
            )
            arrival_days = await self.repository.days_until_next_delivery(
                incident.supplier_id, incident.component_id, incident.scenario_date
            )
        graph_context = graph_task.result()

        risks, risk_warnings = await self.external_risk.evidence(
            upstream_manufacturer_ids=graph_context.upstream_manufacturer_ids,
            region=graph_context.upstream_regions[0] if graph_context.upstream_regions else None,
        )
        warnings = [*data_warnings, *risk_warnings]
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

        inventory_contracts = tuple(
            InventoryPosition(
                component_id=row.component_id,
                location_id=row.location_id,
                on_hand=row.on_hand,
                reserved=row.reserved,
                quarantined=row.quarantined,
                usable=row.usable,
                sources=(csv_source("inventory.csv", f"{row.component_id}|{row.location_id}"),),
            )
            for row in inventory_rows
        )
        total_usable = sum(row.usable for row in inventory_rows)
        shortfall = projected_shortfall(
            total_usable, daily_demand, arrival_days, incident.delay_days
        )
        demand_by_factory = {row.factory_id: row.daily_units for row in factory_demand}
        coverage = tuple(
            CoveragePosition(
                factory_id=row.factory_id,
                location_id=row.location_id,
                usable_units=row.usable,
                daily_demand_units=(
                    demand_by_factory.get(row.factory_id, Decimal(0))
                    if row.factory_id
                    else daily_demand
                ),
                days_of_cover=days_of_cover(
                    row.usable,
                    demand_by_factory.get(row.factory_id, Decimal(0))
                    if row.factory_id
                    else daily_demand,
                ),
                projected_stockout_date=projected_stockout_date(
                    incident.scenario_date,
                    row.usable,
                    demand_by_factory.get(row.factory_id, Decimal(0))
                    if row.factory_id
                    else daily_demand,
                ),
                sources=(csv_source("inventory.csv", row.location_id),),
            )
            for row in inventory_rows
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
        calculations = (
            CalculationTrace(
                calculation="projected_shortfall",
                formula="max(0, daily_demand * (days_to_arrival + delay_days) - usable)",
                inputs={
                    "daily_demand": daily_demand,
                    "days_to_arrival": arrival_days,
                    "delay_days": incident.delay_days,
                    "usable": total_usable,
                },
                result=shortfall,
                unit="units",
                sources=(
                    csv_source("inventory.csv", incident.component_id),
                    csv_source("production_schedule.csv", incident.component_id),
                    csv_source("purchase_orders.csv", incident.component_id),
                ),
            ),
            *expedite_calculations,
        )
        impact = ImpactReport(
            case_id=incident.case_id,
            component_id=incident.component_id,
            inventory=inventory_contracts,
            total_usable_units=total_usable,
            daily_demand_units=daily_demand,
            projected_shortfall_units=shortfall,
            first_stockout_date=projected_stockout_date(
                incident.scenario_date, total_usable, daily_demand
            ),
            affected_product_ids=product_ids,
            affected_factory_ids=graph_context.factory_ids,
            at_risk_orders=at_risk_orders,
            revenue_at_risk_eur=sum((row.revenue_eur for row in orders), start=Decimal(0)),
            supplier_capabilities=supplier_capabilities,
            open_purchase_orders=open_purchase_orders,
            coverage=coverage,
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
