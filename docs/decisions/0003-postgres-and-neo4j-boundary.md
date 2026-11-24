# ADR 0003: PostgreSQL and Neo4j Boundary

- Status: accepted
- Owner: Raldi

## Context

Operational analysis needs both exact arithmetic and multi-hop supply-chain dependency discovery. Keeping the same fact authoritative in two databases would create reconciliation ambiguity.

## Decision

PostgreSQL is the system of record for numerical and operational facts. Neo4j is an idempotently rebuilt relationship projection containing stable IDs and provenance. The application uses reviewed, fixed, parameterized Cypher only. PostgreSQL wins on a mismatch, which is surfaced as a warning rather than hidden.

## Alternatives

- PostgreSQL only: recursive joins can answer paths, but are harder to explain and extend for dependency analysis.
- Neo4j only: graph traversal is strong, but it is a poor sole authority for tabular ingestion, constraints, and financial arithmetic.
- Dual authority: flexible, but produces unclear ownership and risky silent disagreement.

## Consequences

Each store has one defensible purpose. Neo4j can be rebuilt without losing authoritative data. The team must monitor graph/relational mismatches and preserve provenance during projection.
