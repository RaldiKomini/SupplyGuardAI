<!-- Produced by CaseWorkflow (workflow/service.py) running the MC-17 case end to end with the
test stand-ins from tests/workflow/conftest.py and MC-17 reference data. Regenerate it with the
full stack (real impact, evidence and planning services) before the final demonstration. -->

# Incident report: case CASE-GOLD-001

## Summary
Atlas Components (S001) reported a 12-day delay on MC-17 (Motor controller board 48V). The approved plan was executed (EUR 29,400).

## Facts and sources
- Spending limit: EUR 75,000 (message)
- Atlas delay notice: supplier_notice, DOC-NOT-S001 v1, effective 2026-10-19
- Atlas account manager e-mail: email, DOC-EML-ATLAS
- Supply contract - Cordillera Industrial: contract, DOC-CON-S004-v1 v1, effective 2025-01-01

## Operational impact
- Usable stock: 3,200 units; daily demand 450 units
- Projected shortfall: 5,350 units; first stockout not projected
- Affected products: P012, P023, P031, P047, P058, P066; factories: F1, F2, F3
- Open customer orders at risk: 0
- Revenue at risk (exposure, not an expected loss): EUR 0
- Sources: no source given

## Conflicting evidence
- Atlas delay: Formal notice says 12 days; e-mail says 16 days. Resolution: The formal notice is the planning baseline; 16 days is a sensitivity.

## Security findings
- prompt_injection: DOC-NOT-HELIOS-PRICE contains embedded instructions; ignored. (blocked)

## Investigation findings
- [warning] Katowice and Lyon have the lowest cover and run out first; prioritise hub transfers there. (inventory.csv)
- [warning] Delta shares upstream manufacturer Veltro with Atlas, so it is weak diversification. (graph:SOURCES_FROM)

## Options compared
| Option | Recovery cost | Revenue at risk | Uncovered units | Rejection reasons |
|---|---|---|---|---|
| Hub transfer + expedite PO-2026-9002 + Cordillera order | EUR 30,378 | EUR 0 | 850 | - |

## Recommendation
Hub transfer + expedite PO-2026-9002 + Cordillera order: Cheapest feasible plan under the cap.

## Approval record
- Request APR-CASE-GOLD-001-v1 (version 1, hash 9f237d035707): EUR 29,400, needs procurement_manager
- approved at 2026-10-19 14:30 UTC by u-pm-01 (procurement_manager), u-fc-01 (finance_controller)

## Execution outcome
- purchase_alternative ACT-1: succeeded - Purchase order created.

## Residual risks
- none

## Lessons learned
- Sources disagreed on Atlas delay.

