# Recovery planning, approval and procurement execution

Athanasia's component turns a verified impact report into an approved, executed recovery. It implements the shared `RecoveryPlanningService`, `ApprovalService` and `ProcurementExecutionService` protocols and never calculates impact or retrieves evidence itself.

## Flow

1. Planning (`planning/`) builds at least three strategies from the impact report and the cost service: stock transfer, alternative suppliers, expedites and their combinations. Suppliers are screened for certification, ERP status, lead time, capacity and minimum order, and a shared upstream manufacturer is flagged as weak diversification. When required data such as the expected arrival date is missing, the plan says so and recommends nothing.
2. The challenger (`challenger/`) checks the recommendation before anyone sees it: number of strategies, spending cap, cost integrity, revenue confused with cost, missing sources, unknown or uncertified suppliers and capacity. A blocking finding sends the plan back for revision.
3. Approval (`approval/`) creates a request bound to the exact payload hash and stores one decision per approver. Edits are re-priced, re-validated and become a new version. See [ADR 0005](../decisions/0005-exact-payload-approval-and-run-once-execution.md).
4. Execution (`procurement/`) runs only through the procurement MCP and only for a stored, fully approved, current request. Every action runs once per idempotency key.

## Running the procurement MCP

Create the approval tables once, then start the server:

```powershell
uv run --env-file .env alembic upgrade head
uv run --env-file .env python -m supplyguard.procurement.mcp_server
```

It listens on `PROCUREMENT_MCP_HOST:PROCUREMENT_MCP_PORT` (default `127.0.0.1:8100`) and the workflow reaches it at `PROCUREMENT_MCP_URL` (default `http://127.0.0.1:8100/mcp`).

| Tool | Kind | Purpose |
|---|---|---|
| `get_inventory` | read | Stock per location for a component |
| `get_supplier_options` | read | Supplier capabilities for a component |
| `get_open_purchase_orders` | read | Open purchase orders for a component |
| `get_execution_result` | read | Stored outcome for an idempotency key |
| `execute_approved_request` | write | Runs a stored, approved request; takes only `approval_request_id` |

The ERP is simulated: references start with `SIM-PO`, `SIM-EXP` and `SIM-TR`.

## Tracing

Service calls are traced to LangSmith as separate runs: `recovery_planning.propose`, `approval.create_request`, `approval.record_decision`, `approval.record_modification`, `approval.revalidate`, `procurement.execute` and `procurement.execute_approved`, each tagged with its component. Only allowlisted fields are sent (ids, roles, statuses, costs, counts, dates); evidence text, messages, reasons and action payloads never leave the process.

Set `LANGSMITH_TRACING=true`, `LANGSMITH_API_KEY` and `LANGSMITH_PROJECT` in `.env`. Keep tracing off while running the test suite.

## Evaluation experiment

The 105-case suite runs as a LangSmith experiment on the dataset `supplyguard-eval-105`, using the same evaluators as the local harness:

```powershell
docker compose --env-file .env up -d --wait postgres neo4j
uv run --env-file .env python -m supplyguard.evaluation.langsmith_experiment
```

Each case is scored `passed` and `not_runnable`, so cases whose component is not wired yet stay visible instead of being skipped. The command prints the per-category table and the experiment name to compare in LangSmith. Load the support data first (see the operational-intelligence guide); on an empty database every impact case fails.

Result on 25 November 2026 (experiment `supplyguard-eval-1d7d6c53`):

| Category | Total | Passed | Failed | Not runnable yet |
|---|---:|---:|---:|---:|
| golden | 1 | 1 | 0 | 0 |
| extraction_and_impact | 64 | 64 | 0 | 0 |
| prompt_injection | 10 | 10 | 0 | 0 |
| policy_and_authorization | 10 | 6 | 0 | 4 |
| ambiguity | 10 | 0 | 0 | 10 |
| restart_recovery | 6 | 0 | 0 | 6 |
| follow_up | 4 | 0 | 0 | 4 |

All 81 runnable cases pass and none fail. The 24 not runnable cases are reported, not skipped: ambiguity needs LLM intake extraction in the harness, restart and follow-up need the case workflow wired into the harness, and four policy cases need application-layer authorization scopes (EV-0097), evidence retrieval with contract versions and source precedence (EV-0099, EV-0100) and the late-penalty calculation (EV-0101).

## Tests

- `tests/approval/test_service.py`: two approvers authorise the EUR 30,378 plan, a retry creates no duplicates, edits need fresh approval, superseded requests cannot run.
- `tests/workflow/test_two_approvers.py`: the same through the LangGraph workflow.
- `tests/integration/test_recovery_flow.py`: the flow on PostgreSQL (`RUN_INTEGRATION=1`).
- `tests/approval/test_tracing.py`: one run per service call and no confidential text in traces.
- `tests/evaluation/test_langsmith_experiment.py`: the experiment keeps the harness verdicts.
