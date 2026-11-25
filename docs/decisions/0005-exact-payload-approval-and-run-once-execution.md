# ADR 0005: Exact-Payload Approval and Run-Once Procurement Execution

- Status: accepted
- Owner: Athanasia

## Context

A recovery plan turns into real purchase orders, expedites and stock transfers. A human must approve exactly what will run, the approval tier depends on the total cost (procurement manager up to EUR 25,000, procurement manager and finance controller up to EUR 75,000, finance director up to EUR 90,000), and the agent, the user interface or a retry must never be able to change the actions after approval or run them twice.

## Decision

Bind every approval to a SHA-256 hash of the canonical action payload (`approval/hashing.py`). An approval request stores the ordered actions, the hash, a payload version, the total cost recomputed by the cost service and the required roles. Each approver's decision is stored separately; a two-role tier is satisfied only by two different users holding the two roles, and any reject or modify blocks the request. An edit is re-priced and re-validated against stock, supplier capacity and the spending cap, and becomes a new version that needs fresh approval. Older versions are superseded.

Execution goes through the procurement MCP and takes only the approval request id. The server loads the stored request and decisions from PostgreSQL, refuses superseded or not fully approved requests, and claims each action under the idempotency key `case_id:approval_request_id:action_id` before the simulated ERP write. A retry returns the stored outcome with status `already_executed` and never repeats the write.

## Alternatives

- Approve the plan by id only: simpler, but a changed action list would still carry the approval.
- Trust the decision sent by the caller: fewer database reads, but a forged or stale decision could trigger a write.
- Deduplicate in the client: works for one process, but not across restarts or several clients.
- One approval record per request: cannot express two distinct approvers for one tier.

## Consequences

Approvals are auditable and cannot be reused for a different payload. Retries and restarts are safe because the database key, not the caller, decides whether an action already ran. Every edit costs another approval round. The MCP depends on the approval tables (migration `0002_procurement_records`), and a crash between the external write and recording its outcome still needs the external system to deduplicate on the same key, as described in [approval persistence](../architecture/approval-persistence.md).
