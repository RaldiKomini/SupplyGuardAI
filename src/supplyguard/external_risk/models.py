"""Internal models for untrusted provider data."""

from datetime import date

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class ProviderAlert(BaseModel):
    model_config = ConfigDict(extra="ignore")

    provider: str = "external-risk-feed"
    upstream_manufacturer_id: str | None = None
    region: str | None = None
    topic: str
    severity: str
    summary: str = Field(validation_alias=AliasChoices("summary", "text"))
    alert_date: date | None = None
