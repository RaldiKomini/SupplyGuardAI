# SupplyGuard AI

SupplyGuard AI is the capstone project for the Code.Hub x PwC Agentic AI Bootcamp. It investigates supply-chain disruptions, calculates operational impact, compares recovery strategies, and prepares an evidence-backed action plan for human approval.

The reference scenario concerns a 12-day delay of component MC-17 from Atlas Components. The system must identify affected products and customer commitments, evaluate feasible recovery options, enforce policy and authorization rules, and execute only an approved payload.

## Engineering principles

- Business calculations use deterministic Python or SQL tools, not model arithmetic.
- Every material claim preserves provenance.
- Retrieved documents and tool output are untrusted data.
- Human approval binds to the exact action payload.
- Changed quantities, prices, suppliers, or conditions require revalidation and new approval.
- Procurement writes are idempotent and auditable.
- Workflow state survives application restarts.
- The supplied support data is loaded and reasoned over without modification.

## Component ownership

| Owner | Component |
|---|---|
| Elena | Workflow, agent architecture, case memory, and reporting |
| Raldi | Operational data, deterministic impact, Neo4j, and external-risk MCP |
| George | Document intelligence, retrieval, provenance, and content guardrails |
| Athanasia | Recovery planning, approval, and procurement MCP execution |
| Elpida | Chainlit, identity, platform, LangSmith, evaluation, and delivery |

The detailed non-overlapping responsibilities are defined in [COLLABORATION.md](COLLABORATION.md).

## Shared data flow

```text
Chainlit and identity
        |
        v
LangGraph case workflow
        |
        +--> Operational impact and Neo4j
        +--> Document evidence and retrieval
        |
        v
Recovery planning and recommendation challenge
        |
        v
Exact-payload human approval
        |
        v
Idempotent procurement MCP execution
        |
        v
Incident report and approved resolution memory
```

Cross-component models live in `src/supplyguard/contracts`. Components communicate through these models and the protocols in `src/supplyguard/contracts/protocols.py`; they must not import another owner's internal implementation.

## Repository layout

```text
src/supplyguard/
  contracts/        Shared cross-component models and protocols
  workflow/         Elena
  llm/              Elena
  reporting/        Elena
  data/             Raldi
  repositories/     Raldi
  impact/           Raldi
  graph/            Raldi
  external_risk/    Raldi
  retrieval/        George
  vectorstores/     George
  guardrails/       George
  planning/         Athanasia
  approval/         Athanasia
  procurement/      Athanasia
  app/              Elpida
  auth/             Elpida
  observability/    Elpida
  evaluation/       Elpida
```

Directories are created by their owners when implementation begins. The repository starts with shared contracts only.

## Local development

Requirements:

- Python 3.13
- [uv](https://docs.astral.sh/uv/)

```powershell
uv sync --all-groups
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

Copy `.env.example` to `.env` when service implementation begins. Never commit `.env` or credentials.

## Working agreement

1. Start work from the latest `main`.
2. Use the branch assigned to your component.
3. Change shared contracts in a focused contract PR before relying on the change.
4. Include tests, rationale, provenance implications, and integration impact in every PR.
5. Obtain review from the owner of any interface you consume or change.
6. Merge only after CI passes and the component owner accepts the result.

See [COLLABORATION.md](COLLABORATION.md), [SECURITY.md](SECURITY.md), and [the shared-contract guide](docs/architecture/shared-contracts.md) before implementation.

## Operational intelligence

Raldi's component now includes idempotent PostgreSQL loading, deterministic impact calculations, a provenance-aware Neo4j projection, and a cached external-risk MCP client/server. Its service implements the shared `OperationalIntelligenceService` boundary; it measures impact but does not select or execute a recovery plan.

Setup, formulas, failure behaviour, and integration instructions are in [the operational-intelligence guide](docs/architecture/operational-intelligence.md). The main decisions are recorded in [ADR 0002](docs/decisions/0002-operational-data-authority.md) and [ADR 0003](docs/decisions/0003-postgres-and-neo4j-boundary.md).
