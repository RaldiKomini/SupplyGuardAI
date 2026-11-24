"""Small MCP client kept behind a domain-specific method."""

import json
from datetime import timedelta
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from supplyguard.external_risk.models import ProviderAlert


class McpRiskClient:
    def __init__(self, url: str, *, timeout_seconds: float = 5.0) -> None:
        self.url = url
        self.timeout_seconds = timeout_seconds

    async def alerts(self, region: str | None = None) -> tuple[ProviderAlert, ...]:
        async with (
            streamable_http_client(self.url) as (read_stream, write_stream, _),
            ClientSession(
                read_stream,
                write_stream,
                read_timeout_seconds=timedelta(seconds=self.timeout_seconds),
            ) as session,
        ):
            await session.initialize()
            result = await session.call_tool("get_external_risk_alerts", {"region": region})
        payload: Any = getattr(result, "structuredContent", None)
        if payload is None:
            content = getattr(result, "content", ())
            text = next((item.text for item in content if hasattr(item, "text")), "{}")
            payload = json.loads(text)
        records = payload.get("alerts", ())
        return tuple(ProviderAlert.model_validate(record) for record in records)
