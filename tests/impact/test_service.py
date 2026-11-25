from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

import pytest

from supplyguard.contracts import ConfirmedIncident
from supplyguard.graph.client import DependencyContext
from supplyguard.impact.service import DefaultOperationalIntelligenceService
from supplyguard.repositories.operational import (
    CapabilityRow,
    InventoryRow,
    OpenPurchaseOrderRow,
    OrderRow,
    WeeklyDemandRow,
)


class FakeRepository:
    async def inventory_for_component(self, component_id):
        return (
            InventoryRow(component_id, "LOC-1", "FAC-1", 5200, 1400, 600, 3200, date(2025, 1, 1)),
        )

    async def affected_product_ids(self, component_id):
        return ("P-1",)

    async def weekly_component_demand(self, component_id, start_date, end_date):
        return tuple(
            WeeklyDemandRow("FAC-1", start_date + timedelta(days=offset), 3150)
            for offset in range(0, (end_date - start_date).days, 7)
        )

    async def open_orders(self, product_ids, scenario_date):
        return (OrderRow("O-1", "C-1", "P-1", date(2025, 1, 10), 10, 1, Decimal("2500")),)

    async def supplier_capabilities(self, component_id):
        return (
            CapabilityRow(
                "SUP-2", component_id, 5, 3000, 100, Decimal("12.50"), "valid", "active", "UP-1"
            ),
        )

    async def open_purchase_orders(self, component_id):
        return (
            OpenPurchaseOrderRow(
                "PO-2026-9002",
                "S004",
                component_id,
                1500,
                Decimal("9.8"),
                date(2025, 1, 15),
                "open",
                True,
                Decimal("6"),
                7,
            ),
            OpenPurchaseOrderRow(
                "PO-2026-9001",
                "SUP-1",
                component_id,
                6000,
                Decimal("8.50"),
                date(2025, 1, 8),
                "open",
                False,
                Decimal(0),
                0,
            ),
        )

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


@pytest.fixture
def atlas_incident() -> ConfirmedIncident:
    return ConfirmedIncident(
        case_id="CASE-1",
        user_id="U-1",
        supplier_id="SUP-1",
        component_id="MC-17",
        delay_days=12,
        scenario_date=date(2025, 1, 1),
        spending_cap_eur=Decimal("100000"),
        original_message="Atlas says MC-17 is delayed by 12 days.",
    )


@pytest.mark.asyncio
async def test_investigation_builds_traceable_golden_result(atlas_incident) -> None:
    service = DefaultOperationalIntelligenceService(FakeRepository(), FakeGraph(), FakeRisk())
    result = await service.investigate(atlas_incident)

    assert result.impact.total_usable_units == 3200
    assert result.impact.projected_shortfall_units == 5350
    assert result.impact.revenue_at_risk_eur == Decimal("2500")
    assert result.impact.calculations[0].inputs["delay_days"] == 12
    assert result.impact.calculations[0].sources[-1].record_id == "PO-2026-9001"
    assert result.impact.open_purchase_orders[0].po_id == "PO-2026-9002"
    assert result.impact.calculations[1].result == Decimal("882.00")
    assert result.dependencies.disconnected_entity_ids == ("P-99",)
    assert result.warnings == ()


@pytest.mark.asyncio
async def test_late_penalty_scenario_counts_each_tier_one_order_once(atlas_incident) -> None:
    class OrdersWithPenalties(FakeRepository):
        async def open_orders(self, product_ids, scenario_date):
            row = (await super().open_orders(product_ids, scenario_date))[0]
            tier_one = replace(row, weekly_late_penalty_percentage=Decimal(1))
            tier_two = replace(tier_one, order_id="O-2", priority_tier=2)
            return tier_one, tier_one, tier_two

    service = DefaultOperationalIntelligenceService(OrdersWithPenalties(), FakeGraph(), FakeRisk())
    result = await service.investigate(atlas_incident)

    [estimate] = [t for t in result.impact.calculations if t.calculation == "late_penalty_cost"]
    assert estimate.sources[0].record_id == "O-1"
    assert estimate.result == Decimal("50.00")


@pytest.mark.asyncio
async def test_demand_only_counts_days_overlapping_the_arrival_window(atlas_incident) -> None:
    class UnevenSchedule(FakeRepository):
        async def weekly_component_demand(self, component_id, start_date, end_date):
            assert end_date == start_date + timedelta(days=19)
            return (
                WeeklyDemandRow("FAC-1", start_date, 2100),
                WeeklyDemandRow("FAC-1", start_date + timedelta(days=7), 3500),
                WeeklyDemandRow("FAC-1", start_date + timedelta(days=14), 4200),
                WeeklyDemandRow("FAC-1", start_date + timedelta(days=21), 99999),
            )

    result = await DefaultOperationalIntelligenceService(
        UnevenSchedule(), FakeGraph(), FakeRisk()
    ).investigate(atlas_incident)

    # Two full weeks plus five days of the third week.
    assert result.impact.calculations[0].inputs["demand_before_arrival"] == 8600
    assert result.impact.projected_shortfall_units == 5400
    assert result.impact.coverage[0].daily_demand_units == Decimal(8600) / 19


@pytest.mark.asyncio
async def test_stock_mismatch_keeps_verified_locations_and_warns(atlas_incident) -> None:
    class InconsistentStock(FakeRepository):
        async def inventory_for_component(self, component_id):
            return (
                InventoryRow(component_id, "W-STU", "FAC-1", 2600, 0, 0, 2600, None),
                InventoryRow(component_id, "W-KAT", "FAC-2", 1500, 300, 600, 700, None),
            )

    result = await DefaultOperationalIntelligenceService(
        InconsistentStock(), FakeGraph(), FakeRisk()
    ).investigate(atlas_incident)

    assert result.impact.total_usable_units == 2600
    assert [position.location_id for position in result.impact.inventory] == ["W-STU"]
    assert [position.location_id for position in result.impact.coverage] == ["W-STU"]
    assert result.impact.first_stockout_date is None
    assert result.impact.projected_shortfall_units == 5950
    warning = next(w for w in result.warnings if w.issue_type == "inventory_total_unconfirmed")
    assert warning.record_id == "MC-17|W-KAT"
    assert warning.sources[0].source_id == "inventory.csv"


@pytest.mark.asyncio
async def test_named_pending_order_takes_priority_over_earliest(atlas_incident) -> None:
    class TwoAtlasOrders(FakeRepository):
        async def open_purchase_orders(self, component_id):
            orders = await super().open_purchase_orders(component_id)
            later_order = replace(orders[1], po_id="PO-2026-9010", promised_date=date(2025, 1, 10))
            return (*orders, later_order)

    incident = atlas_incident.model_copy(
        update={"original_message": "Atlas says po-2026-9010 is 12 days late."}
    )
    result = await DefaultOperationalIntelligenceService(
        TwoAtlasOrders(), FakeGraph(), FakeRisk()
    ).investigate(incident)

    trace = result.impact.calculations[0]
    assert trace.sources[-1].record_id == "PO-2026-9010"
    assert trace.inputs["window_days"] == 21
    assert result.impact.projected_shortfall_units == 6250


@pytest.mark.asyncio
async def test_named_order_for_other_supplier_does_not_silently_use_earliest(
    atlas_incident,
) -> None:
    incident = atlas_incident.model_copy(
        update={"original_message": "Atlas says PO-2026-9002 is 12 days late."}
    )
    result = await DefaultOperationalIntelligenceService(
        FakeRepository(), FakeGraph(), FakeRisk()
    ).investigate(incident)

    assert result.impact.calculations[0].inputs["window_days"] == 12
    warning = next(w for w in result.warnings if w.issue_type == "delayed_order_unconfirmed")
    assert warning.record_id == "PO-2026-9002"


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
