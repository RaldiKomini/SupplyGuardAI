"""Adapters that expose the evidence layer through the shared workflow protocols
(supplyguard.contracts.protocols), so Elena's workflow can call them without importing
our internal classes:

    ContentSafetyService   -> GuardrailContentSafetyService
    ResolutionMemoryStore  -> EvidenceResolutionMemoryStore

Both are thin wrappers: the logic already lives in guardrails/ and retrieval/service.py.
"""

from __future__ import annotations

import asyncio

from pydantic import ValidationError

from supplyguard.contracts import AuthorizationContext, SecurityFinding
from supplyguard.contracts.memory import ResolutionMemory
from supplyguard.guardrails.injection import scan_text
from supplyguard.guardrails.metadata import SAFE_IDENTIFIER
from supplyguard.retrieval.contract_mapping import to_security_finding
from supplyguard.retrieval.ingestion import parse_memory_chunk
from supplyguard.retrieval.service import EvidenceService


class GuardrailContentSafetyService:
    """`ContentSafetyService`: scan untrusted text, e.g. a supplier notice pasted into the chat
    (eval cases EV-0072..EV-0081). Same detector as document ingestion.

    The text is only scanned, not rewritten: the finding says so ("reported and treated as
    data, never obeyed"), unlike document findings, whose instructions are removed before
    indexing. `blocked` stays True: the instruction is never acted on."""

    def scan(self, text: str, source_id: str) -> tuple[SecurityFinding, ...]:
        # source_id may come from the user; it is shown in finding messages, so it must
        # be a plain identifier (an attack hidden in it would otherwise reach a prompt).
        safe_id = source_id if SAFE_IDENTIFIER.match(source_id or "") else "untrusted_input"
        return tuple(to_security_finding(f, None) for f in scan_text(text, safe_id))


class EvidenceResolutionMemoryStore:
    """`ResolutionMemoryStore`: approved resolution summaries, in the SEPARATE memory
    collection (pgvector table `evidence_incident_memories` or Chroma collection
    `evidence_memories`).

    - save():   the memory goes through the same guardrails as documents (strategy and
                summary scanned, IDs validated) before it is indexed.
    - search(): only memories within the user's allowed scopes; the returned memories
                contain the SANITIZED text that was indexed."""

    def __init__(self, service: EvidenceService):
        self.service = service

    async def save(self, memory: ResolutionMemory) -> None:
        record = memory.model_dump(exclude={"sources"})
        await asyncio.to_thread(self.service.index_approved_memory, record)

    async def search(
        self, query: str, authorization: AuthorizationContext, limit: int = 3
    ) -> tuple[ResolutionMemory, ...]:
        result = await asyncio.to_thread(
            self.service.search_incident_memories, query, authorization, limit
        )
        memories = []
        for evidence in result.evidence:
            fields = parse_memory_chunk(evidence.chunk)
            if fields is None:
                continue
            try:
                memories.append(ResolutionMemory(**fields))
            except ValidationError:
                continue  # e.g. an ID that the guardrail removed: not a usable memory
        return tuple(memories)
