"""Incident contracts owned by the workflow component."""

from datetime import date
from decimal import Decimal

from pydantic import Field

from supplyguard.contracts.common import ContractModel


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
