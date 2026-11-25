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
| Athanasia | Recovery planning, approval, procurement MCP execution, and LangSmith tracing and experiment execution |
| Elpiniki | Chainlit, identity, platform, evaluation, and delivery |

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
  app/              Elpiniki
  auth/             Elpiniki
  observability/    Elpiniki
  evaluation/       Elpiniki
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

## Run the whole system

Copy `.env.example` to `.env` and fill in the secrets and the model settings. Never commit `.env` or credentials. Then:

```powershell
docker compose --profile app up --build -d --wait
```

This starts PostgreSQL, Neo4j and ChromaDB; a one-shot `setup` container runs the migrations, loads the operational data and the graph and indexes the documents (every step can be run again safely); then the risk feed, the external-risk MCP, the procurement MCP and the app start. Open http://127.0.0.1:8000.

Evaluation (all 105 cases, against the running stack): `uv run --env-file .env python -m supplyguard.evaluation.langsmith_experiment --local`. Results: [docs/evaluation/results.md](docs/evaluation/results.md).

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

The component's measurable value hypothesis, KPIs, targets, and attribution limits are documented in [the operational-intelligence business-value assessment](docs/business-value/operational-intelligence.md).

## Recovery, approval and procurement

Athanasia's component plans the recovery, challenges it, binds human approval to the exact action payload, and executes approved actions once through the procurement MCP. It implements the shared `RecoveryPlanningService`, `ApprovalService` and `ProcurementExecutionService` boundaries.

How to run the procurement MCP, the traced service calls, and the LangSmith evaluation experiment are in [the recovery, approval and procurement guide](docs/architecture/recovery-approval-procurement.md). The approval and execution decision is recorded in [ADR 0005](docs/decisions/0005-exact-payload-approval-and-run-once-execution.md).
