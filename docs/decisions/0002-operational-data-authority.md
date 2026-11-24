# ADR 0002: Operational Data Authority and Loading

- Status: accepted
- Owner: Raldi

## Context

The supplied CSVs must remain unchanged, contain deliberate quality problems, and support repeatable loads. Numerical results must be explainable.

## Decision

Use PostgreSQL with SQLAlchemy Core and Alembic. Preserve every row first in a generic JSON staging table with its file hash, then promote valid fields to typed core tables using business-key upserts. Use SQL for filtering and aggregation, and pure Python functions for named business formulas. Attach source references and calculation traces to material results.

## Alternatives

- PostgreSQL without SQLAlchemy: less abstraction, but duplicated connection, typing, and query plumbing.
- ORM models: convenient object navigation, but unnecessary state and implicit loading for a calculation service.
- One typed staging table per CSV: stronger staging types, but duplicates the core schema and cannot preserve malformed input exactly.

## Consequences

Loads are auditable and restart-safe. Raw and typed representations have clear purposes. The generic staging payload needs explicit validation before promotion. Schema changes require an Alembic revision.
