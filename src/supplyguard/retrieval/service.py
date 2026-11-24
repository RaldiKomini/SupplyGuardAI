"""EvidenceService: the evidence layer's implementation of the shared protocol
`supplyguard.contracts.protocols.EvidenceService`.

Public (shared contract) entry point, used by Elena's workflow:

    bundle = await service.retrieve(incident, authorization)      # -> contracts.EvidenceBundle

`authorization` is the AuthorizationContext built by the application layer. We only read its
`allowed_scopes`; we never widen or derive scopes (docs/architecture/shared-contracts.md).

Extra helpers that also return shared contracts:

    service.search_bundle(case_id, "Why was Helios rejected?", authorization)  # follow-ups
    service.search_incident_memories_bundle(case_id, "MC-17 delay", authorization)

Internal helpers (used by the contract agent, tests and the benchmark; return the
internal models in retrieval/models.py):

    service.search(query, scopes), service.supplier_facts("S004", scopes),
    service.index_approved_memory({...}), service.scan_untrusted_text(text)

Everywhere `authorization` / `auth_context` may be the AuthorizationContext, a dict, or a
set of scope names (tests, benchmark). Missing or unknown scopes give no access.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, date, datetime
from pathlib import Path

from supplyguard.contracts import AuthorizationContext, ConfirmedIncident, EvidenceBundle
from supplyguard.guardrails.access import enforce_scope, scopes_from
from supplyguard.guardrails.injection import detect_injection, findings_from_flags
from supplyguard.retrieval.conflicts import extract_facts, resolve
from supplyguard.retrieval.contract_mapping import (
    to_evidence_conflict,
    to_evidence_item,
    to_security_finding,
)
from supplyguard.retrieval.hybrid import Retriever
from supplyguard.retrieval.ingestion import (
    ingest_documents,
    load_incident_memories_with_findings,
    memory_to_chunk_with_findings,
)
from supplyguard.retrieval.lexical import BM25Index
from supplyguard.retrieval.models import (
    Conflict,
    ContentFinding,
    DocumentChunk,
    Evidence,
    RetrievalResult,
    SupplierFacts,
)
from supplyguard.vectorstores.embeddings import get_embeddings
from supplyguard.vectorstores.memory_store import InMemoryVectorStore

DEFAULT_SCENARIO_DATE = date(2026, 10, 19)


def make_vector_store(backend: str, embeddings, collection: str):
    """backend: 'memory', 'pgvector' or 'chroma'. collection: 'documents' or 'memories'."""
    if backend == "pgvector":
        from supplyguard.vectorstores.pgvector_store import (
            DOCUMENTS_TABLE,
            MEMORIES_TABLE,
            PgVectorStore,
        )

        table = DOCUMENTS_TABLE if collection == "documents" else MEMORIES_TABLE
        return PgVectorStore(embeddings, table=table)
    if backend == "chroma":
        from supplyguard.vectorstores.chroma_store import ChromaStore

        return ChromaStore(embeddings, collection=f"evidence_{collection}")
    return InMemoryVectorStore(embeddings, name="memory")


class EvidenceService:
    def __init__(
        self,
        chunks: list[DocumentChunk],
        ingestion_findings: list[ContentFinding],
        document_store,
        memory_store,
        scenario_date: date = DEFAULT_SCENARIO_DATE,
    ):
        self.chunks = chunks
        self.ingestion_findings = ingestion_findings
        self.document_store = document_store
        self.memory_store = memory_store
        self.scenario_date = scenario_date
        self.retriever = Retriever(vector_store=document_store, lexical=BM25Index(chunks))
        # Keyword index of the memory collection. It starts with every memory already in the
        # store, so memories saved before a restart are found by BOTH searches (keyword and
        # vector), not only by the vector one.
        self._memory_chunks: list[DocumentChunk] = list(memory_store.all_chunks())
        self._memory_findings: list[ContentFinding] = []
        self._chunk_by_source = {c.citation.source_id: c for c in chunks}

    # ------------------------------------------------------------------ setup
    @classmethod
    def from_support_data(
        cls,
        support_data_dir: str | Path,
        backend: str = "memory",
        index: bool = True,
        embeddings=None,
        stores: tuple | None = None,
    ) -> EvidenceService:
        """Load documents from disk. With index=True the chunks are (re)written to the
        vector store; use index=False when scripts/index_documents.py already did that.
        `stores` = (document_store, memory_store) to reuse already-open stores (tests use it
        to restart the service on the same in-memory "database")."""
        root = Path(support_data_dir)
        embeddings = embeddings or get_embeddings()
        chunks, findings = ingest_documents(root / "documents")
        if stores is not None:
            doc_store, mem_store = stores
        else:
            doc_store = make_vector_store(backend, embeddings, "documents")
            mem_store = make_vector_store(backend, embeddings, "memories")
        scenario = DEFAULT_SCENARIO_DATE
        policies = root / "config" / "policies.json"
        if policies.exists():
            scenario = date.fromisoformat(json.loads(policies.read_text())["scenario_date"])
        service = cls(chunks, findings, doc_store, mem_store, scenario_date=scenario)
        memories_file = root / "structured" / "approved_incident_memories.jsonl"
        memories, memory_findings = [], []
        if memories_file.exists():
            memories, memory_findings = load_incident_memories_with_findings(memories_file)
        service._add_memory_chunks(memories)
        service._memory_findings = memory_findings
        if index:
            doc_store.add(chunks)
            if memories:
                mem_store.add(memories)
        return service

    # ============================================================ shared protocol
    async def retrieve(
        self,
        incident: ConfirmedIncident,
        authorization: AuthorizationContext,
        alternative_supplier_ids: tuple[str, ...] | None = None,
    ) -> EvidenceBundle:
        """Documentary evidence for a confirmed incident (protocol method).

        Runs in a worker thread so a slow vector store does not block the event loop.
        `alternative_supplier_ids` is optional; if it is not given we use every supplier
        whose notices or e-mails mention the component."""
        return await asyncio.to_thread(
            self.build_incident_bundle, incident, authorization, alternative_supplier_ids
        )

    def build_incident_bundle(
        self,
        incident: ConfirmedIncident,
        authorization,
        alternative_supplier_ids: tuple[str, ...] | None = None,
    ) -> EvidenceBundle:
        scopes = scopes_from(authorization)
        suppliers = [incident.supplier_id]
        alternatives = alternative_supplier_ids or self.suppliers_mentioning(incident.component_id)
        suppliers += [s for s in alternatives if s != incident.supplier_id]

        query = (
            f"{incident.supplier_id} {incident.component_id} delay lead time capacity "
            "expedite certification"
        )
        documents = self.search(query, scopes, k=10)
        procedures = self.search(
            "source precedence qualification expedite shared upstream usable stock approval",
            scopes,
            k=6,
            doc_types=["procedure"],
        )

        # Evidence = the search hits + EVERY document we hold about the affected supplier and
        # the alternatives (contracts, qualification records, notices, e-mails). That way each
        # conflict's selected/conflicting evidence ids point to an item in the bundle, and a
        # document cannot be missed because it ranked low. Scope filtering happens in _to_bundle.
        evidence: list[Evidence] = list(documents.evidence) + list(procedures.evidence)
        conflicts: list[Conflict] = []
        supplier_chunks: list[DocumentChunk] = []
        for sid in suppliers:
            conflicts += self.supplier_facts(sid, scopes).conflicts
            supplier_chunks += [c for c in self.chunks if c.citation.supplier_id == sid]
        return self._to_bundle(
            incident.case_id, query, evidence, supplier_chunks, conflicts, scopes
        )

    # ============================================================ shared-contract helpers
    def search_bundle(self, case_id: str, query: str, authorization, k: int = 8, **filters):
        """Free-text search returned as an EvidenceBundle (e.g. for follow-up questions)."""
        scopes = scopes_from(authorization)
        result = self.search(query, scopes, k=k, **filters)
        return self._to_bundle(case_id, query, result.evidence, [], result.conflicts, scopes)

    def search_incident_memories_bundle(self, case_id: str, query: str, authorization, k=5):
        scopes = scopes_from(authorization)
        result = self.search_incident_memories(query, scopes, k=k)
        return self._to_bundle(
            case_id, query, result.evidence, [], [], scopes, findings=result.security_findings
        )

    # ============================================================ internal API
    def search(
        self,
        query: str,
        auth_context,
        k: int = 8,
        supplier_id: str | None = None,
        doc_types: list[str] | None = None,
    ) -> RetrievalResult:
        """Hybrid search over documents the user is allowed to see.

        The result also lists (a) findings for any returned document that contained an
        injection, and (b) conflicts between the returned sources."""
        scopes = scopes_from(auth_context)
        result = self.retriever.retrieve(
            query, scopes, k=k, supplier_id=supplier_id, doc_types=doc_types
        )
        sources = {e.citation.source_id for e in result.evidence}
        result.security_findings = self._findings_for(sources)
        facts = [f for e in result.evidence for f in extract_facts(e.chunk, self.scenario_date)]
        _, result.conflicts = resolve(facts)
        result.uncertain = bool(result.conflicts)
        if any(e.citation.status == "superseded" for e in result.evidence):
            result.notes.append("Some results are superseded versions, shown for comparison only.")
        if result.security_findings:
            result.notes.append("Embedded instructions were found and removed; they were ignored.")
        if not result.evidence:
            result.notes.append("No authorised evidence found for this query.")
        return result

    def supplier_facts(self, supplier_id: str, auth_context) -> SupplierFacts:
        """All key terms for one supplier, resolved with the precedence rules.

        Reads every chunk of that supplier the user may see (not only the top-k of a
        search), so a conflict can't be missed because one source ranked low."""
        scopes = scopes_from(auth_context)
        mine = [c for c in self.chunks if c.citation.supplier_id == supplier_id]
        mine, _ = enforce_scope(mine, scopes)
        facts = [f for c in mine for f in extract_facts(c, self.scenario_date)]
        planning, conflicts = resolve(facts)
        cert = planning.get("certification_status")
        return SupplierFacts(
            supplier_id=supplier_id,
            planning_values=planning,
            conflicts=conflicts,
            certification_valid=None if cert is None else cert.value == "valid",
            security_findings=self._findings_for({c.citation.source_id for c in mine}),
        )

    def suppliers_mentioning(self, component_id: str) -> tuple[str, ...]:
        """Suppliers whose notices or e-mails mention the component. Contracts are left out
        on purpose: every contract template mentions 'reference component MC-17'."""
        pattern = re.compile(rf"\b{re.escape(component_id)}\b", re.IGNORECASE)
        found = {
            c.citation.supplier_id
            for c in self.chunks
            if c.citation.doc_type in {"supplier_notice", "email"}
            and c.citation.supplier_id
            and pattern.search(c.text)
        }
        return tuple(sorted(found))

    def search_incident_memories(self, query: str, auth_context, k: int = 5) -> RetrievalResult:
        """Semantic + keyword search in the SEPARATE approved-incident-memory collection.

        Like document search, the result lists guardrail findings for every returned
        memory (memories are written from case data, so they are untrusted too)."""
        scopes = scopes_from(auth_context)
        retriever = Retriever(
            vector_store=self.memory_store,
            lexical=BM25Index(self._memory_chunks),
            use_authority=False,
            name="memory-hybrid",
        )
        result = retriever.retrieve(query, scopes, k=k)
        returned = {e.citation.source_id for e in result.evidence}
        findings = [f for f in self._memory_findings if f.source_id in returned]
        # After a restart, memories saved earlier come back from the vector store, but their
        # detailed findings were only in memory. Rebuild them from the flags stored with
        # the chunk, so a finding is never lost.
        known = {f.source_id for f in self._memory_findings}
        for e in result.evidence:
            if e.citation.source_id not in known and e.chunk.security_flags:
                findings += findings_from_flags(e.citation.source_id, e.chunk.security_flags)
        result.security_findings = findings
        if result.security_findings:
            result.notes.append("Embedded instructions were found and removed; they were ignored.")
        return result

    def index_approved_memory(self, record: dict) -> DocumentChunk:
        """Called when Elena's workflow closes an approved case. Required keys:
        memory_id, case_id, approved_summary. Usual keys: component_id, supplier_id,
        strategy. Optional: access_scope (default 'internal'), approved_on (YYYY-MM-DD)."""
        missing = {"memory_id", "case_id", "approved_summary"} - record.keys()
        if missing:
            raise ValueError(f"memory record is missing {sorted(missing)}")
        chunk, findings = memory_to_chunk_with_findings(record)
        self.memory_store.add([chunk])
        self._add_memory_chunks([chunk])
        # Re-indexing the same memory replaces its old findings.
        mid = chunk.citation.source_id
        self._memory_findings = [f for f in self._memory_findings if f.source_id != mid] + findings
        return chunk

    def scan_untrusted_text(self, text: str, source_id: str = "user_attachment"):
        """For content that does not come from our index, e.g. a notice pasted into chat.
        Returns shared-contract SecurityFindings."""
        return tuple(to_security_finding(f, None) for f in detect_injection(text, source_id))

    # ============================================================ private helpers
    def _add_memory_chunks(self, chunks: list[DocumentChunk]) -> None:
        """Add or replace memories in the keyword index (one entry per chunk_id)."""
        new_ids = {c.chunk_id for c in chunks}
        self._memory_chunks = [c for c in self._memory_chunks if c.chunk_id not in new_ids] + chunks

    def _findings_for(self, source_ids: set[str]) -> list[ContentFinding]:
        return [f for f in self.ingestion_findings if f.source_id in source_ids]

    def _to_bundle(self, case_id, query, evidence, cited_chunks, conflicts, scopes, findings=None):
        """Build the shared EvidenceBundle. Relevance scores are rescaled to 0..1 by
        dividing by the best score of this query (the contract requires 0..1).
        `findings`: internal findings to report; by default the document findings for
        the sources in the bundle."""
        now = datetime.now(UTC)
        best = max((e.score for e in evidence), default=0.0) or 1.0
        items, seen = [], set()
        for e in evidence:
            if e.chunk.chunk_id not in seen:
                seen.add(e.chunk.chunk_id)
                items.append(to_evidence_item(e.chunk, e.score / best, now))
        for chunk in cited_chunks:  # cited for a fact, not ranked by a search
            if chunk.chunk_id not in seen:
                seen.add(chunk.chunk_id)
                items.append(to_evidence_item(chunk, None, now))

        # Final safety net: nothing outside the user's scopes leaves this module.
        items = [i for i in items if i.source.access_scope in scopes]
        sources = {i.source.source_id for i in items}

        unique_conflicts = {(c.supplier_id, c.fact_name): c for c in conflicts}.values()
        if findings is None:
            findings = self._findings_for(sources)
        citations = {e.chunk.citation.source_id: e.chunk.citation for e in evidence}
        citations |= {c.citation.source_id: c.citation for c in cited_chunks}
        findings = [
            to_security_finding(f, citations.get(f.source_id))
            for f in findings
            if f.source_id in sources
        ]
        return EvidenceBundle(
            case_id=case_id,
            query=query,
            items=tuple(items),
            conflicts=tuple(to_evidence_conflict(c) for c in unique_conflicts),
            security_findings=tuple(findings),
            applied_access_scopes=tuple(sorted(scopes)),
        )
