# Approval and execution persistence

Run `uv run alembic upgrade head` to create the `procurement` schema in the
existing PostgreSQL database. The support-data loader uses separate schemas.

- `approval_requests` stores one request per case and payload version. Store the
  ordered, JSON-mode action list in `payload` together with its canonical hash,
  required roles and evidence IDs. A modification creates a new request and version.
- `approval_decisions` stores one decision per authenticated user per request.
  The approval service verifies roles and aggregates distinct decisions for the tier.
- `executed_actions` stores one execution record per stable idempotency key and
  prevents a second key from being assigned to the same request action. Store the
  first completed outcome and its external reference here.
- `audit_log` records case-level events with actor and timestamp. Keep credentials
  and confidential document text out of event details.

When wiring execution, claim the action with the stable key before starting work.
Reuse that exact key at the MCP boundary on retries. The MCP must deduplicate the
actual write using the key, including after its own restart. The database constraint
alone cannot prevent a duplicate external write after a crash between the write
and recording its outcome. A replay returns the stored outcome. The
`already_executed` response status does not replace the original stored result.

The demo Finance Director account is `u-fd-01`. Set `AUTH_PASSWORD_U_FD_01` in
your local environment to enable login. The account is disabled by default. The
supplied user CSV stays unchanged. Policy still requires Finance Director approval
for a cap increase above €75,000 and up to €90,000.
