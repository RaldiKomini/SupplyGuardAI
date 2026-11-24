# Operational Intelligence Business Value

Owner: Raldi

## Value hypothesis

Operational intelligence reduces the time between a disruption being confirmed and a decision-maker receiving a quantified, traceable impact assessment. It also reduces avoidable recovery spend by exposing stock, demand, customer commitments, alternative capacity, and shared upstream risk before a plan is approved.

## Measures

| Measure | Definition | Evidence source |
|---|---|---|
| Time to quantified impact | Minutes from confirmed incident to a valid `ImpactReport` | Workflow timestamps |
| Calculation accuracy | Exact match rate on inventory, coverage, shortfall, revenue, penalty, and cost cases | Evaluation suite |
| Evidence coverage | Material numerical claims with both source references and calculation traces | Returned contracts |
| Data-quality visibility | Known relevant quality issues returned rather than silently ignored | Warning contracts |
| Dependency discovery | Known affected products, factories, customers, and upstreams found | Graph evaluation cases |
| Avoided exposure | Revenue/penalty exposure identified before the original manual decision point | Case comparison |
| Recovery-cost delta | Approved recovery cost versus the feasible baseline/manual choice | Approved execution records |

## Baseline and targets

The initial evaluation should record the manual baseline rather than inventing a saving. Proposed acceptance targets are:

- 100% exact arithmetic on deterministic golden cases;
- 100% provenance coverage for material calculated values;
- at least 95% recall on labelled dependency paths;
- zero silent promotion of records with missing required fields;
- external-risk outage does not prevent an internal impact result;
- repeat loading produces no duplicate authoritative facts or graph relationships.

## MC-17 demonstration

The golden case converts a supplier message into a defensible `5,350`-unit projected shortfall, affected products and customer orders, revenue at risk, alternative supplier capability, and the shared Veltro/TW wafer alert. The business claim is not that the system automatically chooses the cheapest action. Its value is that the human and recovery-planning component receive the same measurable facts, assumptions, warnings, and provenance before committing spend.

## Attribution limits

Revenue at risk is exposure, not guaranteed loss. Identified penalties are estimates until the governing contract is validated by the evidence component. Avoided cost is credited only when an approved executed action is compared with a documented baseline. These boundaries prevent inflated ROI claims.
