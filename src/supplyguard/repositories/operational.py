"""SQL queries that assemble calculation inputs without performing business arithmetic."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncConnection

from supplyguard.contracts import DataQualityWarning, Severity, SourceReference
from supplyguard.data.schema import (
    bill_of_materials,
    customer_orders,
    data_quality_issues,
    inventory,
    locations,
    production_schedule,
    purchase_orders,
    supplier_components,
    suppliers,
)


@dataclass(frozen=True)
class InventoryRow:
    component_id: str
    location_id: str
    factory_id: str | None
    on_hand: int
    reserved: int
    quarantined: int
    usable: int
    last_counted_date: date | None


@dataclass(frozen=True)
class OrderRow:
    order_id: str
    customer_id: str
    product_id: str
    due_date: date
    quantity: int
    priority_tier: int
    revenue_eur: Decimal


@dataclass(frozen=True)
class CapabilityRow:
    supplier_id: str
    component_id: str
    lead_time_days: int
    monthly_capacity_units: int
    minimum_order_units: int
    unit_price_eur: Decimal
    certification_status: str
    erp_status: str
    upstream_manufacturer_id: str | None


@dataclass(frozen=True)
class FactoryDemandRow:
    factory_id: str
    daily_units: Decimal


@dataclass(frozen=True)
class OpenPurchaseOrderRow:
    po_id: str
    supplier_id: str
    component_id: str
    quantity: int
    unit_price_eur: Decimal
    promised_date: date
    status: str
    expedite_allowed: bool
    expedite_fee_percentage: Decimal
    expedite_days_gain: int


class OperationalRepository:
    def __init__(self, connection: AsyncConnection) -> None:
        self.connection = connection

    async def inventory_for_component(self, component_id: str) -> tuple[InventoryRow, ...]:
        statement = (
            select(
                inventory.c.component_id,
                inventory.c.location_id,
                locations.c.factory_id,
                inventory.c.on_hand,
                inventory.c.reserved,
                inventory.c.quarantined,
                inventory.c.usable,
                inventory.c.last_counted_date,
            )
            .select_from(inventory.outerjoin(locations))
            .where(inventory.c.component_id == component_id)
            .order_by(inventory.c.location_id)
        )
        return tuple(InventoryRow(*row) for row in (await self.connection.execute(statement)).all())

    async def affected_product_ids(self, component_id: str) -> tuple[str, ...]:
        statement = (
            select(bill_of_materials.c.product_id)
            .where(bill_of_materials.c.component_id == component_id)
            .order_by(bill_of_materials.c.product_id)
        )
        return tuple((await self.connection.execute(statement)).scalars())

    async def average_daily_component_demand(self, component_id: str, start_date: date) -> Decimal:
        statement = (
            select(
                func.coalesce(
                    func.sum(
                        production_schedule.c.planned_units * bill_of_materials.c.qty_per_unit
                    ),
                    0,
                ),
                func.count(func.distinct(production_schedule.c.week_start)),
            )
            .select_from(
                production_schedule.join(
                    bill_of_materials,
                    production_schedule.c.product_id == bill_of_materials.c.product_id,
                )
            )
            .where(
                and_(
                    bill_of_materials.c.component_id == component_id,
                    production_schedule.c.week_start >= start_date,
                )
            )
        )
        units, week_count = (await self.connection.execute(statement)).one()
        return Decimal(units) / Decimal(max(1, week_count)) / Decimal(7)

    async def daily_demand_by_factory(
        self, component_id: str, start_date: date
    ) -> tuple[FactoryDemandRow, ...]:
        statement = (
            select(
                production_schedule.c.factory_id,
                func.sum(production_schedule.c.planned_units * bill_of_materials.c.qty_per_unit),
                func.count(func.distinct(production_schedule.c.week_start)),
            )
            .select_from(production_schedule.join(bill_of_materials))
            .where(
                and_(
                    bill_of_materials.c.component_id == component_id,
                    production_schedule.c.week_start >= start_date,
                )
            )
            .group_by(production_schedule.c.factory_id)
            .order_by(production_schedule.c.factory_id)
        )
        result = await self.connection.execute(statement)
        return tuple(
            FactoryDemandRow(factory_id, Decimal(units) / Decimal(max(1, weeks)) / 7)
            for factory_id, units, weeks in result.all()
        )

    async def open_orders(self, product_ids: Sequence[str]) -> tuple[OrderRow, ...]:
        if not product_ids:
            return ()
        statement = (
            select(
                customer_orders.c.order_id,
                customer_orders.c.customer_id,
                customer_orders.c.product_id,
                customer_orders.c.due_date,
                customer_orders.c.qty,
                customer_orders.c.customer_priority_tier,
                customer_orders.c.revenue_eur,
            )
            .where(
                and_(
                    customer_orders.c.product_id.in_(product_ids),
                    customer_orders.c.status.in_(("open", "confirmed", "in_progress")),
                )
            )
            .order_by(customer_orders.c.due_date, customer_orders.c.order_id)
        )
        return tuple(OrderRow(*row) for row in (await self.connection.execute(statement)).all())

    async def supplier_capabilities(self, component_id: str) -> tuple[CapabilityRow, ...]:
        statement = (
            select(
                supplier_components.c.supplier_id,
                supplier_components.c.component_id,
                supplier_components.c.lead_time_days,
                supplier_components.c.monthly_capacity_units,
                supplier_components.c.moq_units,
                supplier_components.c.unit_price_eur,
                suppliers.c.certification_status,
                suppliers.c.erp_status,
                suppliers.c.upstream_manufacturer_id,
            )
            .select_from(supplier_components.join(suppliers))
            .where(supplier_components.c.component_id == component_id)
            .order_by(supplier_components.c.lead_time_days, supplier_components.c.supplier_id)
        )
        result = await self.connection.execute(statement)
        return tuple(CapabilityRow(*row) for row in result.all())

    async def days_until_next_delivery(
        self, supplier_id: str, component_id: str, scenario_date: date
    ) -> int:
        statement: Select = select(func.min(purchase_orders.c.promised_date)).where(
            and_(
                purchase_orders.c.supplier_id == supplier_id,
                purchase_orders.c.component_id == component_id,
                purchase_orders.c.status.in_(("open", "confirmed", "in_transit")),
                purchase_orders.c.promised_date >= scenario_date,
            )
        )
        promised = (await self.connection.execute(statement)).scalar_one_or_none()
        return max(0, (promised - scenario_date).days) if promised else 0

    async def open_purchase_orders(self, component_id: str) -> tuple[OpenPurchaseOrderRow, ...]:
        statement = (
            select(
                purchase_orders.c.po_id,
                purchase_orders.c.supplier_id,
                purchase_orders.c.component_id,
                purchase_orders.c.qty,
                purchase_orders.c.unit_price_eur,
                purchase_orders.c.promised_date,
                purchase_orders.c.status,
                purchase_orders.c.expedite_allowed,
                purchase_orders.c.expedite_fee_pct,
                purchase_orders.c.expedite_days_gain,
            )
            .where(
                and_(
                    purchase_orders.c.component_id == component_id,
                    purchase_orders.c.status.in_(("open", "confirmed", "in_transit")),
                    purchase_orders.c.supplier_id.is_not(None),
                    purchase_orders.c.promised_date.is_not(None),
                )
            )
            .order_by(purchase_orders.c.promised_date, purchase_orders.c.po_id)
        )
        rows = (await self.connection.execute(statement)).all()
        return tuple(OpenPurchaseOrderRow(*row) for row in rows)

    async def quality_warnings(self, record_ids: Sequence[str]) -> tuple[DataQualityWarning, ...]:
        if not record_ids:
            return ()
        statement = (
            select(
                data_quality_issues.c.issue_type,
                data_quality_issues.c.note,
                data_quality_issues.c.severity,
                data_quality_issues.c.record_ref,
                data_quality_issues.c.file,
            )
            .where(
                or_(
                    *(
                        data_quality_issues.c.record_ref.contains(record_id, autoescape=True)
                        for record_id in record_ids
                    )
                )
            )
            .order_by(data_quality_issues.c.issue_id)
        )
        rows = (await self.connection.execute(statement)).all()
        return tuple(
            DataQualityWarning(
                issue_type=issue_type,
                message=note,
                severity=Severity(severity),
                record_id=record_ref,
                sources=(SourceReference(source_id=file, source_type="csv"),),
            )
            for issue_type, note, severity, record_ref, file in rows
        )
