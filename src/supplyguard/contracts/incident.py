"""Incident contracts owned by the workflow component."""

from datetime import date
from decimal import Decimal
from enum import StrEnum

from pydantic import Field

from supplyguard.contracts.common import ContractModel


class DisruptionType(StrEnum):
    DELAY = "delay"
    QUALITY_HOLD = "quality_hold"
    CAPACITY_SHORTAGE = "capacity_shortage"
    LOGISTICS = "logistics"
    OTHER = "other"


class Urgency(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class SpendingCapSource(StrEnum):
    MESSAGE = "message"
    POLICY_DEFAULT = "policy_default"


class SupplierCandidate(ContractModel):
    """A supplier the user may mean, offered in a clarification question."""

    supplier_id: str = Field(min_length=1)
    name: str = Field(min_length=1)


class ComponentCandidate(ContractModel):
    """A component the user may mean, offered in a clarification question."""

    component_id: str = Field(min_length=1)
    description: str = Field(min_length=1)


class IncidentInput(ContractModel):
    case_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    message: str = Field(min_length=1)
    supplier_query: str | None = None
    component_query: str | None = None
    delay_days: int | None = Field(default=None, ge=0)
    spending_cap_eur: Decimal | None = Field(default=None, ge=0)


class ConfirmedIncident(ContractModel):
    case_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    supplier_id: str = Field(min_length=1)
    component_id: str = Field(min_length=1)
    delay_days: int = Field(ge=0)
    scenario_date: date
    spending_cap_eur: Decimal = Field(ge=0)
    original_message: str = Field(min_length=1)
    supplier_name: str | None = None
    component_description: str | None = None
    disruption_type: DisruptionType = DisruptionType.DELAY
    urgency: Urgency = Urgency.MEDIUM
    spending_cap_source: SpendingCapSource = SpendingCapSource.MESSAGE
    # A limit above the policy cap that the user asked for. It is not applied: raising the
    # cap needs Finance Director approval, decided by the approval rules.
    requested_spending_cap_eur: Decimal | None = Field(default=None, ge=0)
