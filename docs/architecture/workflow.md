# Case workflow, agents, memory and reporting

Owner: Elena. Code: `src/supplyguard/workflow/`, `src/supplyguard/agents/deep_investigation/`,
`src/supplyguard/reporting/`, `src/supplyguard/llm/`.

## Responsibility

The workflow turns a user's message into a case and drives it to an approved, executed and
reported outcome. It calls every other component only through the protocols in
`contracts/protocols.py`, and never calculates, retrieves, plans or writes procurement data
itself.

## The graph

```text
START ─(new case)──────> read_incident ──(unclear, max 2x)──> ask_clarification ⏸ ─┐
  │                           │  ▲─────────────────────────────────────────────────┘
  │                           └─(still unclear)─> stop_intake
  │                      (confirmed)
  │                           ▼
  │        investigate_impact ─> collect_evidence ─> deep_investigation ─> plan_recovery
  │              │ fails                                                   │ fails, no option,
  │              └──────────────────────> stop_case <──────────────────────┘ blocking finding
  │                                                                          or blocking warning
  │                                     request_approval ─> await_approval ⏸ ─> record_decision
  │                                                             ▲   │ wrong request    │
  │                                                             └───┘                  │
  │                         revalidate (modified; invalid edit keeps the request) <────┤
  │                         execute (approved) ─> finish ─> write_report <──(rejected)─┘
  └─(finished case)──> follow_up
```

⏸ marks an `interrupt`: the case is saved and waits for the user.

| Step | Calls | On failure |
|---|---|---|
| `read_incident` | model (structured extraction), `MasterDataService` | asks; stops after 2 rounds |
| `investigate_impact` | `OperationalIntelligenceService` (Raldi) | stop: nothing can be planned without numbers |
| `collect_evidence` | `EvidenceService` with the user's `AuthorizationContext` (George) | continue with no evidence, error recorded |
| `deep_investigation` | Deep Agent over the collected data | continue; findings so far kept |
| `plan_recovery` | `RecoveryPlanningService` (Athanasia) | stop |
| `request_approval` … `execute` | `ApprovalService`, `ProcurementExecutionService` (Athanasia) | refused decision or invalid edit: back to the pause |
| `write_report` | `ResolutionMemoryStore` (George) after a successful execution | |

## Intake

The model only copies what the user wrote into `IncidentExtraction` (strict structured output).
Code then decides:

- a supplier is certain only for an exact name or ID, or a single match; "Atlas" matches two;
- a component is certain only for an explicit ID; a description is always confirmed;
- the supplier must supply the component in master data (`suppliers_of`), otherwise the user
  is asked;
- the spending limit can be lowered by the user, never raised: a higher request is kept in
  `requested_spending_cap_eur` for the Finance Director approval path.

Each new user message, including follow-up questions, is also scanned by
`ContentSafetyService` (George). Embedded
instructions, e.g. a pasted "SYSTEM: reveal…" notice, become security findings in the case
and the report; the extraction prompt treats such text as data.

## Approval, restart and idempotency

- The approval request is built in its own step before the pause, so a restart shows the same
  payload and hash.
- A decision is accepted only for the current request (case, request id, payload hash), then
  passed to the approval rules. A modification is never an approval: it goes through
  `revalidate` and needs a new decision.
- Nothing executes before an accepted approval; a blocking challenge finding or a blocking
  data-quality warning (e.g. unconfirmed stock) stops the case before an approval request exists.
- One case is one LangGraph thread (`thread_id = case_id`). `postgres_checkpointer()` saves
  every finished step, so a restart continues at the next unfinished step without repeating
  finished ones; execution itself is idempotent in the procurement service.
- Runs use `durability="sync"`: each step is saved before the next one starts. LangGraph's
  default (`"async"`) saves while the next step runs, so a crash at that moment could lose
  the step and repeat it after the restart.
- Windows: call `use_windows_compatible_event_loop()` at startup (psycopg async).

## Deep Agent investigation

`deepagents` agent with planning (`write_todos`), notes in sandboxed files kept in its own
state (nothing on disk), and one sub-agent, `evidence-checker`. Its tools are read-only views of
the case data already collected (evidence already filtered by the user's scopes) and
`record_finding`, which refuses findings without sources from the case. It has no procurement,
approval or other write tool. It runs at most `MAX_AGENT_STEPS` graph steps; at the limit the
findings so far are kept. Findings go to the report as "AI investigation notes (unverified)"; planning stays with the
planning service. The agent may only mark findings info or warning (blocking decisions are
deterministic), records at most six, and a finding that calls a supplier's certification
expired is refused when master data says it is valid.

## Application entry point

`CaseWorkflow` implements `CaseWorkflowService` for the Chainlit application. Each graph step
becomes a `UIEvent` (progress, evidence, options, approval, outcome, error). Every method
rejects cases owned by another user, and an approval must include the signed-in user.
During an approval pause, messages are answered without resuming; in a finished case they are
follow-up questions.

## Running with the real components

`open_case_workflow()` (`workflow/runtime.py`) builds the workflow from every owner's real
component, following their integration tests, and closes all connections on exit:

| Slot | Component |
|---|---|
| master data | `PostgresMasterDataService` |
| impact | `DefaultOperationalIntelligenceService` on a new connection per case, Neo4j `DependencyGraph`, external risk over MCP |
| evidence, memory, content safety | `EvidenceService`, `EvidenceResolutionMemoryStore`, `GuardrailContentSafetyService` |
| planning, approval | `DeterministicRecoveryPlanningService`, `DeterministicApprovalService` (Postgres approval records) |
| execution | `McpProcurementClient` |
| checkpoints | `postgres_checkpointer()` |

The approval service revalidates edits against the incident and impact saved in the case,
through `CaseWorkflow.case_context()`.

```python
use_windows_compatible_event_loop()  # at the top of the app module
async with open_case_workflow() as workflow:
    service = CaseService(workflow, ...)

async with open_workflow_parts() as parts:  # evaluation harness
    evaluators.update(parts.evaluators(owner, approvers))
```

`use_windows_compatible_event_loop()` must run at import time of `chainlit_app.py`: Chainlit
imports the app module before it starts its event loop, so the policy applies to that loop.
`open_workflow_parts()` returns the model, services and checkpoints; every workflow built
from them shares the checkpoints, which is what the restart evaluator relies on.

## Evaluation

`workflow/evaluation.py` provides the harness evaluators for the workflow's categories:

| Category | Evaluator | Passes when |
|---|---|---|
| ambiguity | `AmbiguityEvaluator` | intake asks instead of confirming (10/10 with Azure OpenAI) |
| follow_up | `FollowUpEvaluator` | the question is answered from the saved case, and the case is unchanged |
| restart_recovery | `RestartEvaluator` | after stopping at the named step and restarting on the same checkpoints, the case completes, each action runs once, and the approval payload is unchanged |

## Follow-ups and reporting

- A different spending limit reruns the planner on a copy of the incident; nothing is saved or
  executed. A recap is a template. Other questions are answered by the model from the case
  record only, without new calculations.
- The incident report is built from templates over the saved case, so every number and source
  comes from the owning component. After a successful execution the approved summary is saved
  as `ResolutionMemory` (`MEM-<case_id>`, replaced if written again).

## Configuration

`make_model()` is the only place a model is created: `MODEL_PROVIDER`, `MODEL_NAME`,
`MODEL_BASE_URL`, `MODEL_API_KEY`, and `OPENAI_API_VERSION` for Azure OpenAI.

## Tests

`uv run pytest tests/workflow` (no API key or database). The Deep Agent tests drive the real
`deepagents` runtime with a scripted tool-calling model. `RUN_INTEGRATION=1` with Docker
Desktop running adds the real PostgreSQL restart test.

## Limitations

- "Can we protect our highest-priority customers?" is answered from the saved impact data; a
  dedicated reallocation calculation belongs to planning.
- The sample incident report (`docs/architecture/sample-incident-report.md`) was produced with
  test stand-ins; regenerate it with the full stack before the final demonstration.
