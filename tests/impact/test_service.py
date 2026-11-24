from datetime import date
from decimal import Decimal

import pytest

from supplyguard.contracts import ConfirmedIncident
from supplyguard.graph.client import DependencyContext
from supplyguard.impact.service import DefaultOperationalIntelligenceService
from supplyguard.repositories.operational import (
    CapabilityRow,
    FactoryDemandRow,
    InventoryRow,
    OrderRow,
)


class FakeRepository:
    async def inventory_for_component(self, component_id):
        return (
            InventoryRow(component_id, "LOC-1", "FAC-1", 5200, 1400, 600, 3200, date(2025, 1, 1)),
        )

    async def affected_product_ids(self, component_id):
        return ("P-1",)

    async def average_daily_component_demand(self, component_id, start_date):
        return Decimal(450)

    async def daily_demand_by_factory(self, component_id, start_date):
        return (FactoryDemandRow("FAC-1", Decimal(450)),)

    async def open_orders(self, product_ids):
        return (OrderRow("O-1", "C-1", "P-1", date(2025, 1, 10), 10, 1, Decimal("2500")),)

    async def supplier_capabilities(self, component_id):
        return (
            CapabilityRow(
                "SUP-2", component_id, 5, 3000, 100, Decimal("12.50"), "valid", "active", "UP-1"
            ),
        )

    async def days_until_next_delivery(self, supplier_id, component_id, scenario_date):
        return 7

    async def quality_warnings(self, record_ids):
        return ()


class FakeGraph:
    async def context_for(self, supplier_id, component_id):
        return DependencyContext(
            product_ids=("P-1",),
            factory_ids=("FAC-1",),
            customer_ids=("C-1",),
            upstream_manufacturer_ids=("UP-1",),
            upstream_regions=("TW",),
            alternative_supplier_ids=("SUP-2",),
            disconnected_entity_ids=("P-99",),
        )


class FakeRisk:
    async def evidence(self, *, upstream_manufacturer_ids, region=None):
        assert upstream_manufacturer_ids == ("UP-1",)
        assert region == "TW"
        return (), ()


@pytest.mark.asyncio
async def test_investigation_builds_traceable_golden_result() -> None:
    service = DefaultOperationalIntelligenceService(FakeRepository(), FakeGraph(), FakeRisk())
    incident = ConfirmedIncident(
        case_id="CASE-1",
        user_id="U-1",
        supplier_id="SUP-1",
        component_id="MC-17",
        delay_days=12,
        scenario_date=date(2025, 1, 1),
        spending_cap_eur=Decimal("100000"),
        original_message="Atlas says MC-17 is delayed by 12 days.",
    )

    result = await service.investigate(incident)

    assert result.impact.total_usable_units == 3200
    assert result.impact.projected_shortfall_units == 5350
    assert result.impact.revenue_at_risk_eur == Decimal("2500")
    assert result.impact.calculations[0].inputs["delay_days"] == 12
    assert result.dependencies.disconnected_entity_ids == ("P-99",)
    assert result.warnings == ()


@pytest.mark.asyncio
async def test_graph_mismatch_is_visible_but_relational_facts_win() -> None:
    class MismatchedGraph(FakeGraph):
        async def context_for(self, supplier_id, component_id):
            context = await super().context_for(supplier_id, component_id)
            return DependencyContext(
                product_ids=("WRONG",),
                factory_ids=context.factory_ids,
                customer_ids=context.customer_ids,
                upstream_manufacturer_ids=context.upstream_manufacturer_ids,
                upstream_regions=context.upstream_regions,
                alternative_supplier_ids=context.alternative_supplier_ids,
                disconnected_entity_ids=context.disconnected_entity_ids,
            )

    service = DefaultOperationalIntelligenceService(FakeRepository(), MismatchedGraph(), FakeRisk())
    incident = ConfirmedIncident(
        case_id="CASE-1",
        user_id="U-1",
        supplier_id="SUP-1",
        component_id="MC-17",
        delay_days=12,
        scenario_date=date(2025, 1, 1),
        spending_cap_eur=Decimal(0),
        original_message="Delay.",
    )

    result = await service.investigate(incident)

    assert result.impact.affected_product_ids == ("P-1",)
    assert result.warnings[0].issue_type == "graph_relational_mismatch"
