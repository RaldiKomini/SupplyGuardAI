"""SQLAlchemy Core tables for raw staging and authoritative operational facts."""

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Date,
    DateTime,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
)

metadata = MetaData()

raw_records = Table(
    "raw_records",
    metadata,
    Column("dataset_name", String(80), primary_key=True),
    Column("row_number", Integer, primary_key=True),
    Column("record_key", String(200)),
    Column("payload", JSON, nullable=False),
    Column("source_path", Text, nullable=False),
    Column("source_hash", String(64), nullable=False),
    Column("loaded_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    schema="staging",
)

suppliers = Table(
    "suppliers",
    metadata,
    Column("supplier_id", String(40), primary_key=True),
    Column("name", Text, nullable=False),
    Column("country", String(80)),
    Column("city", String(120)),
    Column("erp_status", String(40)),
    Column("upstream_manufacturer_id", String(40)),
    Column("payment_terms", String(80)),
    Column("certification_status", String(40)),
    Column("certification", String(120)),
    Column("certification_expiry", Date),
    schema="core",
)

components = Table(
    "components",
    metadata,
    Column("component_id", String(40), primary_key=True),
    Column("description", Text, nullable=False),
    Column("uom", String(30)),
    Column("standard_cost_eur", Numeric(14, 4), nullable=False),
    schema="core",
)

factories = Table(
    "factories",
    metadata,
    Column("factory_id", String(40), primary_key=True),
    Column("name", Text, nullable=False),
    Column("country", String(80)),
    schema="core",
)

locations = Table(
    "locations",
    metadata,
    Column("location_id", String(40), primary_key=True),
    Column("name", Text, nullable=False),
    Column("factory_id", String(40)),
    schema="core",
)

products = Table(
    "products",
    metadata,
    Column("product_id", String(40), primary_key=True),
    Column("name", Text, nullable=False),
    Column("factory_id", String(40)),
    Column("list_price_eur", Numeric(14, 2), nullable=False),
    schema="core",
)

customers = Table(
    "customers",
    metadata,
    Column("customer_id", String(40), primary_key=True),
    Column("name", Text, nullable=False),
    Column("priority_tier", Integer, nullable=False),
    Column("segment", String(80)),
    schema="core",
)

upstream_manufacturers = Table(
    "upstream_manufacturers",
    metadata,
    Column("upstream_id", String(40), primary_key=True),
    Column("name", Text, nullable=False),
    Column("country", String(80)),
    schema="core",
)

supplier_components = Table(
    "supplier_components",
    metadata,
    Column("supplier_id", String(40), primary_key=True),
    Column("component_id", String(40), primary_key=True),
    Column("unit_price_eur", Numeric(14, 4), nullable=False),
    Column("lead_time_days", Integer, nullable=False),
    Column("monthly_capacity_units", Integer, nullable=False),
    Column("moq_units", Integer, nullable=False),
    Column("approved_source", Boolean, nullable=False),
    schema="core",
)

bill_of_materials = Table(
    "bill_of_materials",
    metadata,
    Column("product_id", String(40), primary_key=True),
    Column("component_id", String(40), primary_key=True),
    Column("qty_per_unit", Numeric(14, 4), nullable=False),
    schema="core",
)

inventory = Table(
    "inventory",
    metadata,
    Column("component_id", String(40), primary_key=True),
    Column("location_id", String(40), primary_key=True),
    Column("on_hand", Integer, nullable=False),
    Column("reserved", Integer, nullable=False),
    Column("quarantined", Integer, nullable=False),
    Column("usable", Integer, nullable=False),
    Column("last_counted_date", Date),
    schema="core",
)

production_schedule = Table(
    "production_schedule",
    metadata,
    Column("product_id", String(40), primary_key=True),
    Column("factory_id", String(40), primary_key=True),
    Column("week_start", Date, primary_key=True),
    Column("planned_units", Integer, nullable=False),
    schema="core",
)

customer_orders = Table(
    "customer_orders",
    metadata,
    Column("order_id", String(40), primary_key=True),
    Column("customer_id", String(40), nullable=False),
    Column("product_id", String(40), nullable=False),
    Column("qty", Integer, nullable=False),
    Column("due_date", Date, nullable=False),
    Column("status", String(40), nullable=False),
    Column("unit_price_eur", Numeric(14, 2), nullable=False),
    Column("revenue_eur", Numeric(16, 2), nullable=False),
    Column("customer_priority_tier", Integer, nullable=False),
    Column("late_penalty_pct_per_week_capped5", Numeric(8, 6), nullable=False),
    schema="core",
)

purchase_orders = Table(
    "purchase_orders",
    metadata,
    Column("po_id", String(40), primary_key=True),
    Column("supplier_id", String(40)),
    Column("supplier_name_as_entered", Text),
    Column("component_id", String(40), nullable=False),
    Column("qty", Integer, nullable=False),
    Column("unit_price_eur", Numeric(14, 4), nullable=False),
    Column("order_date", Date, nullable=False),
    Column("promised_date", Date),
    Column("actual_delivery_date", Date),
    Column("status", String(40), nullable=False),
    Column("expedite_allowed", Boolean, nullable=False),
    Column("expedite_fee_pct", Numeric(8, 6), nullable=False),
    Column("expedite_days_gain", Integer, nullable=False),
    schema="core",
)

reservations = Table(
    "reservations",
    metadata,
    Column("component_id", String(40), primary_key=True),
    Column("location_id", String(40), primary_key=True),
    Column("customer_order_id", String(40), primary_key=True),
    Column("reserved_qty", Integer, nullable=False),
    schema="core",
)

quarantine_lots = Table(
    "quarantine_lots",
    metadata,
    Column("component_id", String(40), primary_key=True),
    Column("location_id", String(40), primary_key=True),
    Column("lot_id", String(60), primary_key=True),
    Column("qty", Integer, nullable=False),
    Column("reason", Text, nullable=False),
    Column("release_expected", Date),
    schema="core",
)

shipment_updates = Table(
    "shipment_updates",
    metadata,
    Column("shipment_event_id", String(60), primary_key=True),
    Column("po_id", String(40), nullable=False),
    Column("supplier_id", String(40)),
    Column("component_id", String(40), nullable=False),
    Column("status", String(40), nullable=False),
    Column("event_date", Date, nullable=False),
    Column("eta", Date),
    Column("carrier", String(120)),
    Column("note", Text),
    schema="core",
)

supplier_message_inbox = Table(
    "supplier_message_inbox",
    metadata,
    Column("message_id", String(60), primary_key=True),
    Column("received", DateTime(timezone=True), nullable=False),
    Column("sender", Text, nullable=False),
    Column("text", Text, nullable=False),
    schema="core",
)

historical_disruption_cases = Table(
    "historical_disruption_cases",
    metadata,
    Column("case_id", String(60), primary_key=True),
    Column("date", Date, nullable=False),
    Column("supplier_id", String(40)),
    Column("component_id", String(40)),
    Column("disruption_type", String(80), nullable=False),
    Column("delay_days", Integer, nullable=False),
    Column("strategy_used", String(120), nullable=False),
    Column("recovery_cost_eur", Numeric(16, 2), nullable=False),
    Column("outcome", Text, nullable=False),
    Column("lesson_learned", Text),
    schema="core",
)

data_sources = Table(
    "data_sources",
    metadata,
    Column("source_id", String(60), primary_key=True),
    Column("description", Text, nullable=False),
    Column("effective_date", Date),
    Column("access_scope", String(80)),
    schema="core",
)

data_quality_issues = Table(
    "data_quality_issues",
    metadata,
    Column("issue_id", Integer, primary_key=True, autoincrement=True),
    Column("issue_type", String(80), nullable=False),
    Column("file", String(120), nullable=False),
    Column("record_ref", String(200)),
    Column("note", Text, nullable=False),
    Column("severity", String(20), nullable=False),
    UniqueConstraint("issue_type", "file", "record_ref", "note", name="uq_data_issue"),
    schema="core",
)

TABLES_BY_DATASET = {
    table.name: table
    for table in (
        suppliers,
        components,
        factories,
        locations,
        products,
        customers,
        upstream_manufacturers,
        supplier_components,
        bill_of_materials,
        inventory,
        production_schedule,
        customer_orders,
        purchase_orders,
        reservations,
        quarantine_lots,
        shipment_updates,
        supplier_message_inbox,
        historical_disruption_cases,
        data_sources,
    )
}
