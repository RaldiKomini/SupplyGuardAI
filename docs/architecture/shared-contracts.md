# Shared Component Contracts

Cross-component communication uses immutable Pydantic models from `src/supplyguard/contracts` and structural protocols from `protocols.py`.

## Contract flow

```text
IncidentInput
  -> (SupplierCandidate / ComponentCandidate when the wording is ambiguous)
  -> ConfirmedIncident
  -> OperationalIntelligenceResult
  -> EvidenceBundle
  -> RecoveryPlan
  -> ApprovalRequest / ApprovalDecision (approve, reject, or modify -> revalidated new request)
  -> ExecutionResult
  -> ResolutionMemory
  -> UIEvent / EvaluationResult
```

## Protocols added for the workflow

| Protocol | Implemented by | Called by |
|---|---|---|
| `MasterDataService` | Raldi | workflow intake (resolving "Atlas" to candidates) |
| `ContentSafetyService` | George | workflow intake (user message), retrieval |
| `ResolutionMemoryStore` | George | workflow (save after execution, search for similar cases) |

`ApprovalDecision.modified_actions` carries the approver's changed actions; the workflow passes
them to the approval service for revalidation and a new approval request.

## Rules

- Models reject unknown fields.
- Models are immutable after validation.
- Monetary values use `Decimal`, never binary floats.
- Dates and timestamps use typed `date` and timezone-aware `datetime` values.
- Material claims carry `SourceReference` values.
- Operational coverage is reported per location through `CoveragePosition`.
- Important numbers include a `CalculationTrace` with formula, inputs, result, unit, and sources.
- Open purchase orders expose expedite eligibility, percentage, and time gain through `OpenPurchaseOrder`.
- Recovery planning calls `OperationalCostService`; it does not import calculation internals.
- For purchase estimates, pass the proposed quantity, selected unit price and its sources in
  `PurchaseCostInput`. For transfers, pass the proposed quantity and trusted policy rate with
  its source in `TransferCostInput`. Both methods return a euro-denominated calculation trace.
- External-risk evidence identifies the relevant upstream manufacturer and alert date.
- Warnings use `DataQualityWarning` or `SecurityFinding` rather than free-form logging only.
- Approval includes a stable hash of the exact canonical action payload.
- Execution includes an idempotency key.
- A component may extend its internal models but must return the shared public contract.

## Ownership

| Contract area | Owner |
|---|---|
| Incident and workflow state boundary | Elena |
| Impact, dependencies, and external risk | Raldi |
| Evidence, conflicts, and content security | George |
| Options, approval, and execution | Athanasia |
| UI and evaluation events | Elpiniki |
| Authorization context and case listing | Elpiniki |
| Workflow entry points (`CaseWorkflowService`) | Elena implements, Elpiniki consumes |

## Authorization

The application layer authenticates the user and builds an immutable `AuthorizationContext` (user, role, factories, allowed scopes). It is passed unchanged to the workflow and to `EvidenceService.retrieve`. An empty `allowed_scopes` means no document access. Components must not widen or derive scopes themselves, and `CaseWorkflowService` implementations must reject cases owned by another user, with one exception: `submit_approval` also accepts an authorized approver (see below).

## Approval and execution

- `ApprovalDecision` records one role per approver. An approval tier that requires two people (for example procurement manager and finance controller above EUR 25,000) must be satisfied by two distinct authenticated users. One user holding two roles does not count twice.
- Case access stays owner-only, but an approver does not need to own the case. `CaseWorkflowService.submit_approval` accepts a decision from a user whose role is in the pending request's `required_approver_roles`, when the request belongs to the decision's case and the `payload_hash` matches. Each approver submits only their own decision, naming only themselves and their own role. The application layer enforces this before calling the workflow; the workflow must not reject such a decision for ownership. Approvers get no other access to the case.
- A decision applies only to the `payload_hash` it names. Changing supplier, quantity, price, or conditions goes through `ApprovalService.revalidate`, which returns a new `ApprovalRequest` with a new hash and `payload_version`. Earlier decisions never carry over.
- `ProcurementExecutionService.execute` returns `tuple[ExecutionResult, ...]`, one result per action. Workflow and UI callers must handle several results, including partial failure. Each result carries `approval_request_id`, `action_id`, `action_type`, and `idempotency_key`.
- Recovery plans carry `challenge_findings`; `RecoveryPlan.needs_revision` is true when any finding is blocking.


Changes follow the contract-change procedure in `COLLABORATION.md`.
