# Time to a decision-ready plan (measured)

What the system time is for the MC-17 case, from the planner's message to an approval request
they can sign. Only measured system time is reported; the manual time for the same work was not
measured, so no saving is claimed.

## How it was measured

Two end-to-end runs on 2026-11-25 against the full stack (PostgreSQL, Neo4j, ChromaDB, risk
feed, external-risk MCP, procurement MCP, Azure OpenAI model). The workflow was called through
`open_case_workflow()` with the MC-17 message from
`support-data/evaluation/golden_scenario.json`; each step's time is the gap between its UI events.
Approvals were submitted by `u-pm-01` and `u-fc-01`, so human decision time is excluded.

## Results

| Step | Run 1 (s) | Run 2 (s) | What runs |
|---|---|---|---|
| `read_incident` | 10.8 | 9.4 | model extraction + master-data lookup |
| `collect_evidence` | 0.1 | 0.1 | pgvector search, access filter, injection check |
| `recall_similar_cases` | 0.0 | 0.0 | memory search |
| `investigate_impact` | 1.8 | 0.5 | SQL impact, Neo4j dependencies, risk MCP |
| `deep_investigation` | 27.6 | 34.2 | Deep Agent (model, read-only tools) |
| `plan_recovery` + approval request | 0.1 | 0.1 | deterministic planner and policy |
| **Message to approval request** | **40.5** | **44.5** | |
| Final approval to executed plan + report | 0.5 | 0.6 | procurement MCP (4 actions), report, memory |

Both runs reproduced the reference numbers (3,200 usable units, 5,350-unit shortfall, the
EUR 30,378 combined plan) and executed 4 of 4 actions.

## Reading the numbers

- The Deep Agent takes 68–77% of the time. Its notes are advisory and the plan does not depend
  on them, so without it the time would be about 10–13 s.
- The deterministic parts (impact, planning, approval, execution) take under 3 s together.
- A value claim needs the manual baseline the [operational intelligence measures](operational-intelligence.md)
  ask for: the time a planner takes today to reach the same approved plan. Until that is
  recorded, this page shows only how fast the system is, not how much time it saves.
