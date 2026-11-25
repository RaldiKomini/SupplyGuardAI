# Evaluation results

All 105 cases of `support-data/evaluation/eval_cases.jsonl`, run on 2026-11-25 against the full
stack (PostgreSQL, Neo4j, ChromaDB, risk feed, external-risk MCP, procurement MCP, Azure OpenAI
model) on branch `feat/final-integration`. Runtime: 7.2 minutes.

```powershell
uv run --env-file .env python -m supplyguard.evaluation.langsmith_experiment --local
```

`--local` runs the same evaluators without uploading to LangSmith; drop it (and set
`LANGSMITH_API_KEY`) to record the run as a LangSmith experiment.

| Category | Total | Passed | Failed | Errored |
|---|---:|---:|---:|---:|
| ambiguity | 10 | 10 | 0 | 0 |
| extraction_and_impact | 64 | 64 | 0 | 0 |
| follow_up | 4 | 4 | 0 | 0 |
| golden | 1 | 1 | 0 | 0 |
| policy_and_authorization | 10 | 10 | 0 | 0 |
| prompt_injection | 10 | 10 | 0 | 0 |
| restart_recovery | 6 | 6 | 0 | 0 |

Overall: **PASSED** (105/105 results recorded)

What the categories check:

- **ambiguity**: unclear messages lead to a question, not a guess.
- **extraction_and_impact**: incident fields and the deterministic impact numbers.
- **follow_up**: answers about a finished case come from its saved state and evidence.
- **golden**: the MC-17 reference case end to end (3,200 usable, 5,350 short, EUR 30,378 plan).
- **policy_and_authorization**: requests the policy forbids are refused (e.g. buying from a
  supplier with an expired certification).
- **prompt_injection**: instructions hidden in documents or messages are blocked and reported.
- **restart_recovery**: a case killed mid-way resumes from its checkpoint with the same
  approval payload and no duplicated action or purchase order.
