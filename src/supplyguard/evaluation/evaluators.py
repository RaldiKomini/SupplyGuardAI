"""Evaluators for the categories that can run against existing components."""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

from supplyguard.approval.policy import ApprovalPolicy
from supplyguard.contracts.events import EvaluationResult, EvaluationStatus
from supplyguard.contracts.incident import ConfirmedIncident
from supplyguard.contracts.protocols import ContentSafetyService, OperationalIntelligenceService
from supplyguard.evaluation.cases import EvaluationCase, EvaluationCategory
from supplyguard.evaluation.policy import PolicyEvaluator
from supplyguard.evaluation.runner import CaseEvaluator, UnavailableEvaluator

EVALUATION_USER_ID = "evaluation-harness"
GOLDEN_SCENARIO_DATE = date(2026, 10, 19)
DEFAULT_SPENDING_CAP_EUR = Decimal("75000")
INJECTION_FINDING_PREFIX = "prompt_injection"


@dataclass(frozen=True)
class GoldenFigures:
    usable_units: int
    shortfall_units: int
    affected_product_ids: frozenset[str]
    open_orders_due_within_60d: int


def load_golden_figures(path: Path) -> GoldenFigures:
    """Read the MC-17 figures from the supplied golden scenario file."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return GoldenFigures(
        usable_units=data["mc17_inventory"]["usable"],
        shortfall_units=data["expected_shortfall_units"],
        affected_product_ids=frozenset(data["affected_products"]),
        open_orders_due_within_60d=data["open_orders_due_within_60d_on_affected_products"],
    )


class ImpactEvaluator:
    """Runs the impact service for an incident and checks the result against the dataset.

    Starts from the expected supplier, component and delay. It tests the impact calculation,
    not the LLM extraction of those fields.
    """

    def __init__(
        self,
        service: OperationalIntelligenceService,
        golden: GoldenFigures | None = None,
        scenario_date: date = GOLDEN_SCENARIO_DATE,
        spending_cap_eur: Decimal = DEFAULT_SPENDING_CAP_EUR,
    ) -> None:
        self.service = service
        self.golden = golden
        self.scenario_date = scenario_date
        self.spending_cap_eur = spending_cap_eur

    async def evaluate(self, case: EvaluationCase) -> EvaluationResult:
        expected = case.expected
        incident = ConfirmedIncident(
            case_id=case.id,
            user_id=EVALUATION_USER_ID,
            supplier_id=expected["supplier_id"],
            component_id=expected["component_id"],
            delay_days=expected["delay_days"],
            scenario_date=self.scenario_date,
            spending_cap_eur=self.spending_cap_eur,
            original_message=case.message,
        )
        impact = (await self.service.investigate(incident)).impact
        affected = set(impact.affected_product_ids)
        open_orders = len(impact.at_risk_orders)

        checks: list[tuple[bool, str]] = []
        if "affected_products" in expected:
            checks.append(
                _check(
                    set(expected["affected_products"]) == affected,
                    f"affected products expected {sorted(expected['affected_products'])}, "
                    f"got {sorted(affected)}",
                )
            )
        if "open_orders_due_within_60d" in expected:
            checks.append(
                _check(
                    expected["open_orders_due_within_60d"] == open_orders,
                    f"open orders due within 60 days expected "
                    f"{expected['open_orders_due_within_60d']}, got {open_orders}",
                )
            )
        if self.golden is not None and case.category is EvaluationCategory.GOLDEN:
            checks.extend(golden_checks(self.golden, impact, affected, open_orders))

        failures = tuple(message for ok, message in checks if not ok)
        if not checks:
            failures = ("case has no expected impact values to check",)
        return EvaluationResult(
            evaluation_case_id=case.id,
            category=case.category.value,
            status=EvaluationStatus.PASSED if not failures else EvaluationStatus.FAILED,
            metrics={
                "checks": float(len(checks)),
                "checks_failed": float(len(failures)),
                "open_orders": float(open_orders),
            },
            findings=failures,
        )


def golden_checks(
    golden: GoldenFigures, impact, affected: set[str], open_orders: int
) -> list[tuple[bool, str]]:
    return [
        _check(
            impact.total_usable_units == golden.usable_units,
            f"usable MC-17 expected {golden.usable_units}, got {impact.total_usable_units}",
        ),
        _check(
            impact.projected_shortfall_units == golden.shortfall_units,
            f"shortfall expected {golden.shortfall_units}, got {impact.projected_shortfall_units}",
        ),
        _check(
            affected == set(golden.affected_product_ids),
            f"golden affected products expected {sorted(golden.affected_product_ids)}, "
            f"got {sorted(affected)}",
        ),
        _check(
            open_orders == golden.open_orders_due_within_60d,
            f"golden open orders expected {golden.open_orders_due_within_60d}, got {open_orders}",
        ),
    ]


def _check(ok: bool, message: str) -> tuple[bool, str]:
    return ok, message


class PromptInjectionEvaluator:
    """Passes when the untrusted text is flagged as a blocked prompt-injection finding."""

    def __init__(self, safety: ContentSafetyService) -> None:
        self.safety = safety

    async def evaluate(self, case: EvaluationCase) -> EvaluationResult:
        findings = [
            finding
            for finding in self.safety.scan(case.message, case.id)
            if finding.finding_type.startswith(INJECTION_FINDING_PREFIX)
        ]
        flagged = bool(findings) and all(finding.blocked for finding in findings)
        return EvaluationResult(
            evaluation_case_id=case.id,
            category=case.category.value,
            status=EvaluationStatus.PASSED if flagged else EvaluationStatus.FAILED,
            metrics={"injection_findings": float(len(findings))},
            findings=()
            if flagged
            else ("embedded instruction was not flagged as a blocked security finding",),
        )


UNAVAILABLE_REASONS: Mapping[EvaluationCategory, str] = {
    EvaluationCategory.AMBIGUITY: (
        "needs LLM intake extraction, which is not wired into the harness yet"
    ),
    EvaluationCategory.RESTART_RECOVERY: (
        "needs the case workflow checkpoints, which is not implemented yet"
    ),
    EvaluationCategory.FOLLOW_UP: (
        "needs the case workflow with persisted case state, which is not implemented yet"
    ),
}


def default_evaluators(
    impact_service: OperationalIntelligenceService,
    safety: ContentSafetyService,
    golden: GoldenFigures | None,
    approval_policy: ApprovalPolicy | None = None,
) -> dict[EvaluationCategory, CaseEvaluator]:
    impact = ImpactEvaluator(impact_service, golden=golden)
    evaluators: dict[EvaluationCategory, CaseEvaluator] = {
        EvaluationCategory.GOLDEN: impact,
        EvaluationCategory.EXTRACTION_AND_IMPACT: impact,
        EvaluationCategory.PROMPT_INJECTION: PromptInjectionEvaluator(safety),
    }
    for category, reason in UNAVAILABLE_REASONS.items():
        evaluators[category] = UnavailableEvaluator(reason)
    evaluators[EvaluationCategory.POLICY_AND_AUTHORIZATION] = (
        PolicyEvaluator(impact_service, approval_policy)
        if approval_policy is not None
        else UnavailableEvaluator("needs the approval policy from support-data/config")
    )
    return evaluators
