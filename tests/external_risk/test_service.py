from datetime import UTC, datetime, timedelta

import pytest

from supplyguard.contracts import Severity
from supplyguard.external_risk.models import ProviderAlert
from supplyguard.external_risk.service import ExternalRiskService


class FakeClient:
    def __init__(self) -> None:
        self.calls = 0

    async def alerts(self, region=None):
        self.calls += 1
        return (
            ProviderAlert(
                upstream_manufacturer_id="UP-1",
                region="TW",
                topic="wafer supply",
                severity="high",
                summary="Lead time increased by two weeks.",
            ),
        )


def test_supplied_feed_text_field_maps_to_summary() -> None:
    alert = ProviderAlert.model_validate(
        {
            "region": "TW",
            "topic": "wafer supply",
            "severity": "high",
            "text": "Lead time increased.",
        }
    )

    assert alert.summary == "Lead time increased."


@pytest.mark.asyncio
async def test_five_minute_cache_and_upstream_filter() -> None:
    now = datetime(2025, 1, 1, tzinfo=UTC)
    client = FakeClient()
    service = ExternalRiskService(client, clock=lambda: now)

    first, warnings = await service.evidence(upstream_manufacturer_ids=("UP-1",), region="TW")
    second, _ = await service.evidence(upstream_manufacturer_ids=("UP-1",), region="TW")

    assert client.calls == 1
    assert first == second
    assert first[0].retrieved_at == now
    assert warnings == ()


@pytest.mark.asyncio
async def test_expired_cache_refreshes() -> None:
    times = iter(
        (
            datetime(2025, 1, 1, tzinfo=UTC),
            datetime(2025, 1, 1, tzinfo=UTC) + timedelta(minutes=6),
        )
    )
    client = FakeClient()
    service = ExternalRiskService(client, clock=lambda: next(times))

    await service.evidence(upstream_manufacturer_ids=())
    await service.evidence(upstream_manufacturer_ids=())

    assert client.calls == 2


@pytest.mark.asyncio
async def test_provider_failure_degrades_to_warning() -> None:
    class FailingClient:
        async def alerts(self, region=None):
            raise TimeoutError

    evidence, warnings = await ExternalRiskService(FailingClient()).evidence(
        upstream_manufacturer_ids=()
    )

    assert evidence == ()
    assert warnings[0].severity is Severity.WARNING
    assert warnings[0].issue_type == "external_risk_unavailable"
