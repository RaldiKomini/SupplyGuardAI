"""Identity and case-listing contracts shared by the application layer and the workflow."""

from enum import StrEnum

from pydantic import AwareDatetime, Field, field_validator

from supplyguard.contracts.common import ContractModel


class AuthorizationContext(ContractModel):
    """Authenticated user's permissions, built by the application layer and passed downstream."""

    user_id: str = Field(min_length=1)
    role: str = Field(min_length=1)
    factory_ids: tuple[str, ...] = ()
    allowed_scopes: tuple[str, ...] = ()

    @field_validator("factory_ids", "allowed_scopes", mode="after")
    @classmethod
    def normalise_values(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        stripped = {value.strip() for value in values}
        if "" in stripped:
            raise ValueError("values must not be empty")
        return tuple(sorted(stripped))


class CaseStatus(StrEnum):
    IN_PROGRESS = "in_progress"
    AWAITING_INPUT = "awaiting_input"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"


class CaseSummary(ContractModel):
    case_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    status: CaseStatus
    stage: str = Field(min_length=1)
    updated_at: AwareDatetime
