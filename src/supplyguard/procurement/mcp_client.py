"""Procurement execution for the workflow, through the procurement MCP server."""

import json
from datetime import timedelta
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from supplyguard.contracts import ApprovalDecision, ApprovalRequest, ExecutionResult
from supplyguard.tracing import traced


class McpProcurementClient:
    """Implements ProcurementExecutionService by calling execute_approved_request.

    Only the request id is sent. The server loads the request and the approvers'
    decisions from the approval tables, so the decision argument is not trusted.
    """

    def __init__(self, url: str, *, timeout_seconds: float = 10.0) -> None:
        self.url = url
        self.timeout_seconds = timeout_seconds

    @traced("procurement.execute", component="procurement")
    async def execute(
        self, request: ApprovalRequest, decision: ApprovalDecision
    ) -> tuple[ExecutionResult, ...]:
        payload = await self._call(
            "execute_approved_request",
            {"approval_request_id": request.approval_request_id},
        )
        return tuple(ExecutionResult.model_validate(item) for item in payload["results"])

    async def _call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        async with (
            streamable_http_client(self.url) as (read_stream, write_stream, _),
            ClientSession(
                read_stream,
                write_stream,
                read_timeout_seconds=timedelta(seconds=self.timeout_seconds),
            ) as session,
        ):
            await session.initialize()
            result = await session.call_tool(tool, arguments)
        if result.isError:
            text = next((item.text for item in result.content if hasattr(item, "text")), "")
            raise RuntimeError(f"procurement MCP tool {tool} failed: {text}")
        payload: Any = result.structuredContent
        if payload is None:
            text = next((item.text for item in result.content if hasattr(item, "text")), "{}")
            payload = json.loads(text)
        return payload
