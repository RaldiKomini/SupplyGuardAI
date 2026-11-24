# ADR 0001 Contract First Foundation

## Status

Accepted for repository bootstrap.

## Context

Five developers must build independent components in parallel. The system combines workflow orchestration, operational calculations, retrieval, knowledge graphs, approval, MCP integrations, UI, and evaluation. Direct imports between unfinished components would create merge conflicts and unclear ownership.

## Decision

The repository uses immutable shared Pydantic models and typing protocols as the only supported cross-component boundary. Each component remains behind a protocol and may initially be represented by a mock. Shared-contract changes use focused PRs before component implementations depend on them.

## Alternatives considered

- A single shared implementation branch: rejected because it prevents parallel ownership.
- Passing untyped dictionaries: rejected because payload drift would be discovered late.
- Directly importing another component's internal classes: rejected because it couples implementation and ownership.

## Consequences

- Teams can develop and test against mocks.
- Contract changes require coordination.
- Additional mapping may be required between internal and public models.
- Integration errors should appear earlier and be easier to assign to an owner.

## Owner

Elena owns the central workflow contracts; each domain owner owns their contract area as listed in the shared-contract guide.
