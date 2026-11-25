"""RecoveryPlanningService for the workflow: build the options, then challenge them."""

import json
from decimal import Decimal
from pathlib import Path

from supplyguard.challenger.rules import challenge_plan
from supplyguard.contracts import ConfirmedIncident, EvidenceBundle, RecoveryPlan
from supplyguard.contracts.impact import OperationalIntelligenceResult
from supplyguard.contracts.protocols import OperationalCostService
from supplyguard.planning.options import PlanningInputs, build_recovery_plan


class DeterministicRecoveryPlanningService:
    """Implements RecoveryPlanningService with the rule-based planner and challenger.

    All numbers come from the impact report and the cost service. The evidence bundle
    is not used for numbers; it is accepted to match the protocol.
    """

    def __init__(self, costs: OperationalCostService, transfer_cost_eur_per_unit: Decimal) -> None:
        self.costs = costs
        self.transfer_cost_eur_per_unit = transfer_cost_eur_per_unit

    @classmethod
    def from_policy_file(
        cls, path: Path, costs: OperationalCostService
    ) -> "DeterministicRecoveryPlanningService":
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(costs, Decimal(str(data["transfer_cost_eur_per_unit"])))

    async def propose(
        self,
        incident: ConfirmedIncident,
        intelligence: OperationalIntelligenceResult,
        evidence: EvidenceBundle,
    ) -> RecoveryPlan:
        inputs = PlanningInputs(
            incident=incident,
            impact=intelligence.impact,
            costs=self.costs,
            transfer_cost_eur_per_unit=self.transfer_cost_eur_per_unit,
        )
        return challenge_plan(
            build_recovery_plan(inputs),
            spending_cap_eur=incident.spending_cap_eur,
            capabilities=intelligence.impact.supplier_capabilities,
        )
