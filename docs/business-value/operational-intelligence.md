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

The integration test loaded the supplied data into PostgreSQL and Neo4j and queried
the risk feed over MCP. From the confirmed incident it returned:

- 3,200 usable units, 450 units of daily demand, and a 5,350-unit projected shortfall;
- six affected products and 77 open customer orders due in the 60-day exposure window;
- two open purchase orders, including the €882 expedite estimate for PO-2026-9002;
- Delta's shared upstream dependency and the high-severity Taiwan wafer alert.

These results reproduce the reference case and give the planner traceable inputs
before any spending decision. The measured system time for the whole case is in
[time to a decision-ready plan](time-to-decision.md); manual comparison time and realized
savings remain unmeasured. The integration test covers the operational service after confirmation.

## Attribution limits

Revenue at risk is exposure, not guaranteed loss. Identified penalties are estimates until the governing contract is validated by the evidence component. Avoided cost is credited only when an approved executed action is compared with a documented baseline. These boundaries prevent inflated ROI claims.
