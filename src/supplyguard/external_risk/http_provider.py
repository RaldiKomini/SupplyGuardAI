"""Adapter for the supplied independent HTTP risk-feed service."""

import httpx

from supplyguard.external_risk.models import ProviderAlert


class HttpRiskProvider:
    def __init__(self, base_url: str, *, timeout_seconds: float = 5.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    async def alerts(self, region: str | None = None) -> tuple[ProviderAlert, ...]:
        params = {"region": region} if region else None
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.get(f"{self.base_url}/alerts", params=params)
            response.raise_for_status()
            payload = response.json()
        records = payload.get("alerts", payload) if isinstance(payload, dict) else payload
        return tuple(ProviderAlert.model_validate(record) for record in records)
