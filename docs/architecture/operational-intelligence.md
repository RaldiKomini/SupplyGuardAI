# Operational Intelligence

Owner: Raldi

This component answers one bounded question: **what is the measurable operational impact of a confirmed disruption, and what dependencies or outside risks change that impact?** It does not choose a recovery action.

## Public boundary

Elena's workflow calls `DefaultOperationalIntelligenceService.investigate(ConfirmedIncident)`. The service returns the shared `OperationalIntelligenceResult` contract. No other component should query these tables or Neo4j directly.

## Internal flow

```text
ConfirmedIncident
    |-- PostgreSQL repositories --> inventory, demand, orders, capabilities
    |-- fixed Neo4j queries ------> products, factories, customers, upstreams
    `-- external-risk MCP --------> current upstream/region evidence
                 |
                 v
       deterministic Python calculations
                 |
                 v
 OperationalIntelligenceResult + sources + calculation traces + warnings
```

PostgreSQL is authoritative for quantities, dates, prices, status, and other operational facts. Neo4j is a derived projection for multi-hop paths. If they disagree, the result uses PostgreSQL facts and emits `graph_relational_mismatch`.

## Data loading

`staging.raw_records` keeps each CSV row as exact JSON plus filename and SHA-256. A dataset reload replaces only that dataset's staging rows. Typed `core` tables then upsert on supplied business keys, so restarts are safe.

Run migrations and loaders:

```powershell
$env:DATABASE_URL = "postgresql://supplyguard:change-me@127.0.0.1:5432/supplyguard"
uv run alembic upgrade head
uv run supplyguard-load-data C:\path\to\support-data

$env:NEO4J_URI = "bolt://127.0.0.1:7687"
$env:NEO4J_USER = "neo4j"
$env:NEO4J_PASSWORD = "change-me"
uv run supplyguard-load-graph C:\path\to\support-data
```

The loaders read but never modify the supplied files. Missing primary IDs and invalid inventory balances are blocking findings: the raw record remains staged, but it is not promoted as an authoritative fact. Stale or conflicting data is returned as a warning.

## Calculations

Business formulas live in `supplyguard.calculations`; they have no database, network, or model dependency. Important results include their formula, typed inputs, result, unit, and sources.

The MC-17 golden scenario verifies:

- usable stock = `5,200 - 1,400 - 600 = 3,200` units;
- coverage = `3,200 / 450 = 7.1` days;
- stock at original arrival = `3,200 - (450 * 7) = 50` units;
- disruption-window demand = `450 * 12 = 5,400` units;
- projected shortfall = `5,400 - 50 = 5,350` units.

## Neo4j safety and provenance

The graph loader uses parameterized `UNWIND` plus `MERGE`, making repeated runs idempotent. Nodes and relationships store `sourceId` and `effectiveDate`. The public graph facade exposes named operations only; it never accepts model-generated or caller-provided Cypher.

## External-risk MCP

The MCP server exposes one read-only tool, `get_external_risk_alerts`. It uses the MCP 1.x line supported by the current Chainlit runtime and wraps the supplied HTTP feed rather than copying that service's data into application logic. The client caches each region for five minutes and records the retrieval timestamp. Timeout, protocol, or provider failure yields `external_risk_unavailable` and the investigation continues.

Start it with:

```powershell
$env:EXTERNAL_RISK_FEED_URL = "http://127.0.0.1:8003"
uv run supplyguard-risk-mcp
```

## Testing

```powershell
uv run pytest
uv run ruff check .
uv run ruff format --check .

# Optional real PostgreSQL and Neo4j tests; requires Docker
$env:RUN_INTEGRATION = "1"
uv run pytest -m integration
```

Unit tests use fakes at I/O boundaries. Opt-in container tests verify idempotent PostgreSQL loading and Neo4j path queries against real engines.
