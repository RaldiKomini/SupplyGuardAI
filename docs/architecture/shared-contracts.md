# Shared Component Contracts

Cross-component communication uses immutable Pydantic models from `src/supplyguard/contracts` and structural protocols from `protocols.py`.

## Contract flow

```text
IncidentInput
  -> ConfirmedIncident
  -> OperationalIntelligenceResult
  -> EvidenceBundle
  -> RecoveryPlan
  -> ApprovalRequest / ApprovalDecision
  -> ExecutionResult
  -> UIEvent / EvaluationResult
```

## Rules

- Models reject unknown fields.
- Models are immutable after validation.
- Monetary values use `Decimal`, never binary floats.
- Dates and timestamps use typed `date` and timezone-aware `datetime` values.
- Material claims carry `SourceReference` values.
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
| UI and evaluation events | Elpida |
| Authorization context and case listing | Elpida |
| Workflow entry points (`CaseWorkflowService`) | Elena implements, Elpida consumes |

## Authorization

The application layer authenticates the user and builds an immutable `AuthorizationContext` (user, role, factories, allowed scopes). It is passed unchanged to the workflow and to `EvidenceService.retrieve`. An empty `allowed_scopes` means no document access. Components must not widen or derive scopes themselves, and `CaseWorkflowService` implementations must reject cases owned by another user.

Changes follow the contract-change procedure in `COLLABORATION.md`.
