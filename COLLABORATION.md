# SupplyGuard AI Collaboration Contract

This file is the team's working agreement. It defines ownership, boundaries, branches, interface changes, reviews, and the evidence required before merge.

## Ownership rule

Every implementation area has one primary owner. Other developers may contribute after coordinating with the owner, but they do not create competing implementations or silently change the owner's interface.

Reviewing a component does not create shared ownership. Integration means connecting published contracts, not copying another component's internal logic.

## Roles and exclusive scopes

### Elena — workflow, agents, memory, and reporting

Branch: `feat/workflow-agents-memory`

Owns:

- Incident extraction and clarification.
- Shared workflow state and routing.
- LangGraph nodes, interrupts, checkpoints, and restart recovery.
- LangChain Core abstractions and the configurable model interface.
- Deep Agents investigation orchestration.
- Follow-up questions from persisted case state.
- Central workflow integration.
- Incident-report and approved-summary generation.

Does not own operational calculations, retrieval indexes, recovery decisions, procurement execution, or UI/platform code.

### Raldi — operational intelligence

Branch: `feat/operational-intelligence`

Owns:

- PostgreSQL schemas, migrations, loaders, and repositories.
- Structured-data validation and quality warnings.
- Deterministic inventory, demand, impact, revenue, penalty, and cost tools.
- Neo4j loading, provenance, and dependency queries.
- External-risk MCP implementation.
- Golden-scenario numerical verification.
- Business-value assessment.

Does not select a recovery plan or implement workflow, retrieval, approval, procurement writes, or UI code.

### George — evidence and content safety

Branch: `feat/evidence-retrieval-guardrails`

Owns:

- Document parsing, chunking, and metadata preservation.
- pgvector, ChromaDB, and hybrid retrieval.
- Approved-incident-memory retrieval collection.
- Source precedence, version handling, conflict reporting, and citations.
- Retrieval-time access-scope filtering.
- Bounded `create_agent` contract specialist.
- Retrieved-content prompt-injection guardrails.
- Retrieval benchmark and red-team findings.

Does not authenticate users, calculate operational impact, select actions, or execute procurement operations.

### Athanasia — recovery and transaction safety

Branch: `feat/recovery-approval-procurement`

Owns:

- Recovery-option generation and comparison.
- Capacity, timing, certification, policy, and spending validation.
- Recommendation challenge and rejection reasons.
- Exact-payload approval requests and reapproval rules.
- Approval-tier validation and approval records.
- Procurement MCP read and write operations.
- Idempotent action execution and outcome recording.

Does not own UI approval controls, deterministic calculations, workflow routing, retrieval, or service deployment.

### Elpida — product surface and delivery

Branch: `feat/platform-evaluation-delivery`

Owns:

- Chainlit application and approval interactions.
- Authentication, sessions, roles, case access, and authorization context.
- Application-service boundary.
- Docker, configuration, health checks, and startup order.
- LangSmith tracing and experiment execution.
- The 105-case evaluation harness and final quality report.
- Root documentation, demo script, release checklist, and limitations.

Does not implement workflow internals, retrieval ranking, calculations, recovery decisions, or procurement actions.

## Shared files

The following files affect all components:

- `src/supplyguard/contracts/**`
- `pyproject.toml`
- `.env.example`
- `README.md`
- `COLLABORATION.md`
- `SECURITY.md`
- `.github/workflows/**`

Changes to a shared file require a focused PR and an explanation of compatibility and migration impact. Do not mix a contract change with unrelated feature work.

## Contract-change procedure

1. Open a focused PR describing the required behaviour.
2. Change or add the shared model or protocol.
3. Add contract tests.
4. State whether the change is backward compatible.
5. Identify every consuming component.
6. Obtain review from the affected owners.
7. Merge the contract PR before implementation PRs rely on it.

Existing fields must not be renamed or removed without a migration plan. Prefer additive changes with explicit defaults when they preserve the intended safety guarantees.

## Branch and pull-request workflow

```text
main
  feat/workflow-agents-memory
  feat/operational-intelligence
  feat/evidence-retrieval-guardrails
  feat/recovery-approval-procurement
  feat/platform-evaluation-delivery
  fix/<short-description>
  contract/<short-description>
  docs/<short-description>
```

Rules:

- Never develop directly on `main` after the initial repository bootstrap.
- Rebase or merge the latest `main` before requesting final review.
- Keep PRs focused on one component or one contract change.
- Avoid drive-by formatting in files outside the PR scope.
- Do not commit the unmodified support-data archive unless the team explicitly decides its repository location.
- Do not modify the supplied synthetic data.

## Pull-request evidence

Every PR must explain:

- The owner and component.
- The business requirement addressed.
- The design decision and rejected alternatives.
- Public contracts added or changed.
- Provenance and authorization implications.
- Failure and restart behaviour.
- Tests run and their results.
- Integration assumptions and remaining limitations.

The template in `.github/pull_request_template.md` enforces this structure.

## Definition of done

A component change is done only when:

- The implementation stays inside its ownership boundary.
- Public inputs and outputs use shared contracts.
- Unit tests cover normal, boundary, and failure behaviour.
- External writes are idempotent where applicable.
- Claims and calculations preserve provenance.
- Secrets and confidential values do not appear in logs or commits.
- Documentation explains how another component calls it.
- CI passes.
- The accountable owner approves the result.

## Integration rule

Components integrate through the protocols in `src/supplyguard/contracts/protocols.py`.

- Elena connects the workflow-level calls.
- Elpida connects the application and deployment boundaries.
- Each domain owner remains responsible for the correctness of their returned contract.

No component may reach into another owner's database tables or internal classes when a public repository, service, or protocol exists.

## Decision records

Material architectural choices require a short ADR under `docs/decisions/`. Record the context, decision, alternatives, consequences, and owner. The first ADR establishes the contract-first structure.
