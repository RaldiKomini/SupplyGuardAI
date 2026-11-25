# Security and Safety Baseline

SupplyGuard AI handles synthetic project data, but its design must enforce production-style safety boundaries.

## Required controls

- Treat retrieved documents, email, web content, and tool output as untrusted data.
- Never allow retrieved content to override system instructions, policies, authorization, or approval requirements.
- Filter documents by the authenticated user's allowed scopes before model context construction.
- Bind human approval to the exact supplier, quantity, price, date, conditions, and action payload.
- Revalidate and request new approval when the payload changes.
- Require approval for every procurement write.
- Use idempotency keys for writes and return the first result for a repeated key.
- Associate every case and checkpoint with its owning user.
- Keep finance-confidential data out of unauthorized responses, traces, and logs.
- Store secrets only in environment variables or an approved secret manager.
- Do not commit `.env`, API keys, passwords, tokens, or exported confidential traces.

## Responsibility boundaries

- George owns retrieved-content guardrails and red-team findings.
- Elpiniki owns identity, sessions, roles, case access, and authorization context.
- Athanasia owns transaction approval, policy validation, and idempotent procurement execution.
- Elena owns user-bound case recovery and safe workflow resumption.
- Raldi owns structured-data validation and deterministic numerical integrity.

## Reporting a problem

Do not publish sensitive details in a public issue. Contact the repository owner privately, describe the affected component, reproduction steps, expected result, and observed result.
