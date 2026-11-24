"""Five-minute cache and graceful-failure policy for MCP risk evidence."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol

from supplyguard.contracts import DataQualityWarning, RiskEvidence, Severity, SourceReference
from supplyguard.external_risk.models import ProviderAlert


class RiskClient(Protocol):
    async def alerts(self, region: str | None = None) -> tuple[ProviderAlert, ...]: ...


class ExternalRiskService:
    def __init__(
        self,
        client: RiskClient,
        *,
        ttl: timedelta = timedelta(minutes=5),
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.client = client
        self.ttl = ttl
        self.clock = clock
        self._cache: dict[str | None, tuple[datetime, tuple[ProviderAlert, ...]]] = {}

    async def evidence(
        self,
        *,
        upstream_manufacturer_ids: tuple[str, ...],
        region: str | None = None,
    ) -> tuple[tuple[RiskEvidence, ...], tuple[DataQualityWarning, ...]]:
        now = self.clock()
        cached = self._cache.get(region)
        try:
            if cached and now - cached[0] < self.ttl:
                retrieved_at, alerts = cached
            else:
                alerts = await self.client.alerts(region)
                retrieved_at = now
                self._cache[region] = (retrieved_at, alerts)
        except Exception as error:  # network/protocol errors are deliberately non-fatal
            warning = DataQualityWarning(
                issue_type="external_risk_unavailable",
                message=f"External risk evidence is unavailable: {type(error).__name__}.",
                severity=Severity.WARNING,
                sources=(SourceReference(source_id="external-risk-mcp", source_type="mcp"),),
            )
            return (), (warning,)

        relevant = (
            tuple(
                alert
                for alert in alerts
                if not alert.upstream_manufacturer_id
                or alert.upstream_manufacturer_id in upstream_manufacturer_ids
            )
            if upstream_manufacturer_ids
            else alerts
        )
        evidence = tuple(self._to_evidence(alert, retrieved_at) for alert in relevant)
        return evidence, ()

    @staticmethod
    def _to_evidence(alert: ProviderAlert, retrieved_at: datetime) -> RiskEvidence:
        return RiskEvidence(
            provider=alert.provider,
            upstream_manufacturer_id=alert.upstream_manufacturer_id,
            region=alert.region,
            topic=alert.topic,
            severity=alert.severity,
            summary=alert.summary,
            alert_date=alert.alert_date,
            retrieved_at=retrieved_at,
            sources=(
                SourceReference(
                    source_id="external-risk-mcp",
                    source_type="mcp",
                    record_id=alert.upstream_manufacturer_id,
                    effective_date=alert.alert_date,
                    retrieved_at=retrieved_at,
                ),
            ),
        )
