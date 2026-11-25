"""Test stand-ins for the workflow: a scripted model and fake teammate services.

The fakes only exist for tests; the real services are the other owners' components.
"""

import csv
import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from langchain_core.runnables import RunnableLambda

from supplyguard.contracts import (
    ApprovalRequest,
    ChallengeFinding,
    ComponentCandidate,
    DataQualityWarning,
    DependencyReport,
    EvidenceBundle,
    ExecutionResult,
    ExecutionStatus,
    ImpactReport,
    OperationalIntelligenceResult,
    RecoveryAction,
    RecoveryActionType,
    RecoveryOption,
    RecoveryPlan,
    Severity,
    SupplierCandidate,
)
from supplyguard.workflow.services import WorkflowServices
from supplyguard.workflow.settings import DATA_DIR


def _rows(name: str) -> list[dict]:
    with open(DATA_DIR / "structured" / name, encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


class FakeMasterData:
    """Name lookups over the supplied CSVs: suppliers whose name starts with the query words,
    components whose ID matches or whose description contains every query word."""

    def __init__(self) -> None:
        self.suppliers = [
            SupplierCandidate(supplier_id=r["supplier_id"], name=r["name"])
            for r in _rows("suppliers.csv")
        ]
        self.components = [
            ComponentCandidate(component_id=r["component_id"], description=r["description"])
            for r in _rows("components.csv")
        ]
        self.links = _rows("supplier_components.csv")

    async def find_suppliers(self, query: str) -> tuple[SupplierCandidate, ...]:
        words = query.lower().split()
        return tuple(
            s
            for s in self.suppliers
            if s.supplier_id.lower() == query.lower().strip()
            or s.name.lower().split()[: len(words)] == words
        )

    async def find_components(self, query: str) -> tuple[ComponentCandidate, ...]:
        words = [w for w in query.lower().replace("-", " ").split() if w not in {"the", "a"}]
        exact = [c for c in self.components if c.component_id.lower() == query.lower().strip()]
        described = [c for c in self.components if all(w in c.description.lower() for w in words)]
        return tuple(exact or described)

    async def suppliers_of(self, component_id: str) -> tuple[SupplierCandidate, ...]:
        ids = {r["supplier_id"] for r in self.links if r["component_id"] == component_id}
        return tuple(s for s in self.suppliers if s.supplier_id in ids)


class ScriptedModel:
    """Returns the given extractions in order, one per call."""

    def __init__(self, *extractions) -> None:
        self.extractions = list(extractions)

    def with_structured_output(self, schema, **kwargs):
        return RunnableLambda(lambda _prompt: self.extractions.pop(0))


class FakeIntelligence:
    """A few MC-17 reference numbers; counts calls to show a step is not repeated."""

    def __init__(self, *, blocking_warning: str | None = None) -> None:
        self.calls = 0
        self.blocking_warning = blocking_warning

    async def investigate(self, incident) -> OperationalIntelligenceResult:
        self.calls += 1
        impact = ImpactReport(
            case_id=incident.case_id,
            component_id=incident.component_id,
            inventory=(),
            total_usable_units=3200,
            daily_demand_units=Decimal("450"),
            projected_shortfall_units=5350,
            affected_product_ids=("P012", "P023", "P031", "P047", "P058", "P066"),
            affected_factory_ids=("F1", "F2", "F3"),
            at_risk_orders=(),
            revenue_at_risk_eur=Decimal("0"),
            warnings=(
                (
                    DataQualityWarning(
                        issue_type="inventory_total_unconfirmed",
                        message=self.blocking_warning,
                        severity=Severity.BLOCKING,
                    ),
                )
                if self.blocking_warning
                else ()
            ),
        )
        dependencies = DependencyReport(case_id=incident.case_id, findings=())
        return OperationalIntelligenceResult(impact=impact, dependencies=dependencies)


class FakeEvidence:
    """Remembers the authorization it received, to check it is passed on unchanged."""

    def __init__(self) -> None:
        self.authorization = None

    async def retrieve(self, incident, authorization) -> EvidenceBundle:
        self.authorization = authorization
        return EvidenceBundle(case_id=incident.case_id, query="MC-17 delay", items=())


CORDILLERA_ORDER = RecoveryAction(
    action_id="ACT-1",
    action_type=RecoveryActionType.PURCHASE_ALTERNATIVE,
    payload={"supplier_id": "S004", "component_id": "MC-17", "qty": 3000},
    cost_eur=Decimal("29400"),
)


class FakePlanning:
    def __init__(self, *, blocking_finding: str | None = None) -> None:
        self.blocking_finding = blocking_finding

    async def propose(self, incident, intelligence, evidence) -> RecoveryPlan:
        option = RecoveryOption(
            option_id="OPT-1",
            name="Hub transfer + expedite PO-2026-9002 + Cordillera order",
            actions=(CORDILLERA_ORDER,),
            recovery_cost_eur=Decimal("30378"),
            revenue_at_risk_eur=Decimal("0"),
            uncovered_units=850,
        )
        findings = ()
        if self.blocking_finding:
            findings = (
                ChallengeFinding(
                    check="test", message=self.blocking_finding, severity=Severity.BLOCKING
                ),
            )
        return RecoveryPlan(
            case_id=incident.case_id,
            options=(option,),
            recommended_option_id="OPT-1",
            recommendation_reason="Cheapest feasible plan under the cap.",
            challenge_findings=findings,
        )


def _hash(actions) -> str:
    payload = [a.model_dump(mode="json") for a in actions]
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:12]


class FakeApproval:
    """Builds requests whose hash changes with the actions; records every decision."""

    def __init__(
        self, *, refuse_with: str | None = None, capacity_units: int | None = None
    ) -> None:
        self.recorded = []
        self.requests_created = 0
        self.refuse_with = refuse_with
        self.capacity_units = capacity_units

    def _request(self, case_id, option_id, actions, version) -> ApprovalRequest:
        return ApprovalRequest(
            approval_request_id=f"APR-{case_id}-v{version}",
            case_id=case_id,
            option_id=option_id,
            payload_version=version,
            payload_hash=_hash(actions),
            actions=actions,
            total_cost_eur=sum((a.cost_eur for a in actions), Decimal("0")),
            required_approver_roles=("procurement_manager",),
            evidence_source_ids=(),
            requested_at=datetime(2026, 10, 19, tzinfo=UTC),
        )

    async def create_request(self, plan) -> ApprovalRequest:
        self.requests_created += 1
        option = next(o for o in plan.options if o.option_id == plan.recommended_option_id)
        return self._request(plan.case_id, option.option_id, option.actions, 1)

    async def record_decision(self, request, decision):
        if self.refuse_with:
            raise ValueError(self.refuse_with)
        self.recorded.append(decision)
        return decision

    async def revalidate(self, request, modified_actions) -> ApprovalRequest:
        too_many = [
            a for a in modified_actions if a.payload.get("qty", 0) > (self.capacity_units or 10**9)
        ]
        if too_many:  # like the real RevalidationError, a ValueError
            raise ValueError(f"Cordillera can supply at most {self.capacity_units} units per month")
        version = request.payload_version + 1
        return self._request(request.case_id, request.option_id, modified_actions, version)

    async def record_modification(self, request, decision) -> ApprovalRequest:
        revised = await self.revalidate(request, decision.modified_actions)
        await self.record_decision(request, decision)
        return revised


class FakeExecution:
    """Idempotent like the real one: a repeated key returns the first result, writes nothing."""

    def __init__(self) -> None:
        self.writes = []
        self.results = {}

    async def execute(self, request, decision) -> tuple[ExecutionResult, ...]:
        out = []
        for action in request.actions:
            key = f"{request.case_id}:{action.action_id}:v{request.payload_version}"
            if key in self.results:
                out.append(self.results[key].model_copy(update={"status": "already_executed"}))
                continue
            self.writes.append(action.payload)
            result = ExecutionResult(
                case_id=request.case_id,
                approval_request_id=request.approval_request_id,
                action_id=action.action_id,
                action_type=action.action_type,
                idempotency_key=key,
                status=ExecutionStatus.SUCCEEDED,
                message="Purchase order created.",
            )
            self.results[key] = result
            out.append(result)
        return tuple(out)


class FailingService:
    """Every call fails, like a service that is down."""

    async def investigate(self, incident):
        raise RuntimeError("database unavailable")

    async def retrieve(self, incident, authorization):
        raise RuntimeError("vector store unavailable")

    async def propose(self, incident, intelligence, evidence):
        raise RuntimeError("planner unavailable")


@pytest.fixture
def master_data() -> FakeMasterData:
    return FakeMasterData()


@pytest.fixture
def scripted_model():
    return ScriptedModel


@pytest.fixture
def failing_service() -> FailingService:
    return FailingService()


@pytest.fixture
def blocked_planning() -> FakePlanning:
    return FakePlanning(blocking_finding="Fewer than three strategies were compared.")


@pytest.fixture
def capacity_limited_approval() -> FakeApproval:
    return FakeApproval(capacity_units=3000)


@pytest.fixture
def unconfirmed_stock() -> FakeIntelligence:
    return FakeIntelligence(
        blocking_warning=(
            "Stock at W-KAT is inconsistent and was excluded. "
            "The stock total is a verified subtotal and shortfall is provisional."
        )
    )


@pytest.fixture
def refusing_approval() -> FakeApproval:
    return FakeApproval(refuse_with="a second approver with the finance_controller role is needed")


@pytest.fixture
def make_services(master_data):
    """make_services() gives working fakes; pass e.g. intelligence=... to replace one."""

    def build(**replacements) -> WorkflowServices:
        parts = {
            "master_data": master_data,
            "intelligence": FakeIntelligence(),
            "evidence": FakeEvidence(),
            "planning": FakePlanning(),
            "approval": FakeApproval(),
            "execution": FakeExecution(),
        }
        return WorkflowServices(**(parts | replacements))

    return build
