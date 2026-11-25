# Evidence layer: documents, retrieval, provenance and guardrails

Owner: **George** · Branch: `feat/evidence-retrieval-guardrails`

This component reads the 212 supplied documents, finds the evidence for a case, says which
source wins when sources disagree, keeps confidential documents away from users who may not
see them, and removes instructions hidden in documents.

Other components use it only through the shared protocol
`supplyguard.contracts.protocols.EvidenceService`:

```python
bundle = await evidence_service.retrieve(incident, authorization)  # -> contracts.EvidenceBundle
```

Internally we use richer models (`retrieval/models.py`); `retrieval/contract_mapping.py`
converts them to the shared contracts at the boundary, so nothing internal leaks out.

## 1. How it fits in

```
Elpiniki (auth) ──── AuthorizationContext ──────────┐
                                                     ▼
Elena (workflow) ── ConfirmedIncident ──► EvidenceService.retrieve() ──► EvidenceBundle
                                             │        (contract_mapping.py: EvidenceItem,
                                             │         EvidenceConflict, SecurityFinding)
          ┌──────────────────────────────────┼────────────────────────────┐
          ▼                                  ▼                            ▼
   ingestion.py                        hybrid.py                     conflicts.py
   front matter, supersession,         BM25 + vector + RRF,          facts by regex,
   injection scan, chunking            authority ranking             precedence resolution
          │                                  │
          ▼                                  ▼
   guardrails/injection.py          vectorstores/: pgvector | chroma | in-memory
                                    (scope filter INSIDE every query)
```

`retrieve()` returns, for the affected supplier and every alternative supplier whose notices
or e-mails mention the component: all their documents (contracts incl. superseded versions,
qualification records, notices, e-mails), the best-matching procedures, every conflict with
the value chosen and why, and security findings. Athanasia reads the bundle (she must not
import our internal classes). Elena calls `index_approved_memory()` when a case closes.

## 2. Files

| File | What it does |
|---|---|
| `retrieval/models.py` | Internal models: `Citation`, `DocumentChunk`, `Evidence`, `Fact`, `Conflict`, `SupplierFacts`, `RetrievalResult`, `ContentFinding` |
| `retrieval/contract_mapping.py` | Internal models -> shared `SourceReference`, `EvidenceItem`, `EvidenceConflict`, `SecurityFinding` |
| `retrieval/ingestion.py` | Parses front matter, marks superseded versions, scans for injection, chunks with a provenance header |
| `retrieval/precedence.py` | Source-precedence ranks, protected facts, ranking bonus/penalty |
| `retrieval/conflicts.py` | Reads lead time, capacity, expedite fee, delay and certificate status from text; resolves conflicts |
| `retrieval/lexical.py` | BM25 keyword search (written out, about 40 lines) |
| `retrieval/hybrid.py` | `Retriever`: vector + BM25 + metadata filter, RRF fusion, dedup, authority ranking |
| `retrieval/service.py` | `EvidenceService`: implements the shared protocol (`async retrieve`) |
| `vectorstores/pgvector_store.py` | Baseline index; separate table for approved incident memories |
| `vectorstores/chroma_store.py` | Alternative index, same metadata |
| `vectorstores/memory_store.py` | Same interface, in memory (tests, offline demo) |
| `vectorstores/embeddings.py` | `hash` (offline default), `openai`, or `huggingface` embeddings |
| `guardrails/access.py` | Turns the AuthorizationContext into scopes; final scope check |
| `guardrails/injection.py` | Detects and removes embedded instructions |
| `guardrails/untrusted.py` | Wraps evidence in `<untrusted_document>` tags for prompts |
| `guardrails/red_team.py` | Attack suite + report generator |
| `agents/contract_agent/` | `create_agent` contract specialist (read-only tools, structured output) |
| `scripts/index_documents.py` | Loads documents + memories into pgvector and Chroma |
| `scripts/benchmark_retrieval.py` | pgvector vs Chroma vs hybrid benchmark |
| `tests/evidence/` | Acceptance, red-team, agent and contract-boundary tests |

## 3. Interface for the team

```python
from supplyguard.retrieval.service import EvidenceService

service = EvidenceService.from_support_data("support-data", backend="pgvector", index=False)

# Shared protocol (Elena's document-retrieval node)
bundle = await service.retrieve(incident, authorization)  # AuthorizationContext
bundle.items  # EvidenceItem: content, version, effective_date, status, source
bundle.conflicts  # EvidenceConflict: subject "S004.expedite_fee_pct", selected/conflicting ids
bundle.security_findings  # SecurityFinding: prompt_injection.*, BLOCKING, blocked=True
bundle.applied_access_scopes

# Also returning EvidenceBundle
service.search_bundle(case_id, "Why was Helios rejected?", authorization)  # follow-ups
service.search_incident_memories_bundle(case_id, "MC-17 delay", authorization)  # memories
service.scan_untrusted_text(attached_notice)  # -> tuple[SecurityFinding] (EV-0072..0081)

# Writes into the separate memory collection when a case closes
service.index_approved_memory(
    {
        "memory_id": ...,
        "case_id": ...,
        "component_id": ...,
        "supplier_id": ...,
        "strategy": ...,
        "approved_summary": ...,
    }
)
```

Mapping rules (`contract_mapping.py`):
- `evidence_id` = chunk id (`DOC-CON-S001-v3#c0`); `version` is a string; `relevance_score`
  is scaled to 0..1 by dividing by the best score of the query (`None` = included because a
  fact cites it, not because it ranked).
- `EvidenceConflict.subject` = `"<supplier_id>.<fact>"`; the values are in `description` and
  `resolution_reason`, because the shared contract has no numeric field for them.
- `SecurityFinding.message` never repeats the attack text, because a finding may later be
  shown to a model (e.g. in the incident report).

For a prompt, use `format_evidence_for_prompt(...)` from `guardrails/untrusted.py`.

### Workflow protocols (`retrieval/adapters.py`)

```python
from supplyguard.retrieval.adapters import (
    EvidenceResolutionMemoryStore,  # implements contracts.protocols.ResolutionMemoryStore
    GuardrailContentSafetyService,  # implements contracts.protocols.ContentSafetyService
)

safety = GuardrailContentSafetyService()
findings = safety.scan(user_message, "user_message")  # -> tuple[SecurityFinding, ...]

memories = EvidenceResolutionMemoryStore(service)
await memories.save(resolution_memory)  # guardrails run before indexing
similar = await memories.search("MC-17 delay", authorization, limit=3)  # -> ResolutionMemory
```

- `scan()` uses the same detector as ingestion. A negated safety rule in the user's own text
  ("Do not create a purchase order without approval") is not flagged. `scan()` does not
  rewrite the text, so its findings say "reported and treated as data, never obeyed";
  `blocked` is True because the instruction is never acted on (the evaluation harness
  relies on this).
- `save()` scans `strategy` and `approved_summary`, validates the IDs, and writes to the
  separate memory collection. `search()` returns only memories within the user's scopes,
  with the sanitized text that was indexed. Memories come back from the stored chunk text,
  so they are found again after a restart. The `sources` of a saved memory are not stored.
- Memory searches also report guardrail findings (`search_incident_memories`, `..._bundle`).
- After a restart, memories already in pgvector/Chroma are reloaded into the keyword index
  (`all_chunks()`), and their findings are rebuilt from the stored `security_flags`.

## 4. Design decisions (and why)

**Provenance header on every chunk.** Each chunk starts with
`[DOC-CON-S001-v3 | contract | v3 | effective 2026-03-01 | supplier S001]`. A claim can never
be separated from its source, even if a chunk is copied into a prompt on its own.

**Supersession at ingestion.** A contract is superseded if its front matter says so, or if the
same supplier has a contract with a later effective date and higher version. Superseded
documents stay searchable (EV-0099 "use the old 10-day lead time" must find v2 to reject it),
but they are marked and ranked lower.

**Precedence as code, not as a prompt** (DOC-PRO-02): contract > qualification > notice >
e-mail > chat; newer effective date wins inside a type. `supplier_facts()` reads every document
of a supplier (not just the top search hits), so a conflict cannot be missed because a source
ranked low.

| Conflict | Planning value | Kept as |
|---|---|---|
| Atlas lead time v3 14 d vs v2 10 d | 14 (v3) | v2 ignored: superseded |
| Atlas delay notice 12 d vs e-mail 16 d | 12 (notice) | 16 = sensitivity |
| Cordillera expedite contract 6% vs e-mail 10% | 6% (contract) | 10% = sensitivity |
| Helios capacity contract 4,000 vs e-mail 1,500 | 4,000 (contract, PRO-02) | 1,500 = sensitivity / risk |

For Helios, PRO-02 says e-mail quantities never override contractual capacity without written
confirmation, so the planning value is 4,000. The 1,500 is still reported, and the recovery-planning
layer should use it as the downside case, so a "real" capacity is never hidden.

**Protected facts.** Certification status can only come from a qualification record; spending
caps and approval rules only from procedures. So the Helios notice cannot "mark Helios as
certified" even if the regex missed the attack. This is enforced in `precedence.py`.

**Hybrid retrieval.** BM25 is good at exact IDs (`MC-17`, `PO-2026-9002`, `S004`); vectors are good at
paraphrases. Reciprocal Rank Fusion (score = Σ 1/(60+rank)) combines them using ranks only,
so we never compare a BM25 score with a cosine similarity. Then a small authority bonus and a
superseded penalty are added. They are small on purpose, so relevance comes first.

**Access scope filtered before the model.** The filter runs inside the pgvector SQL `WHERE`,
the Chroma `where` filter and before BM25 scoring, then once more on the final list. The
contract agent's tools have the user's scopes built in: the model has no scope parameter to change.

**Injection guardrail.** Hidden HTML comments are always removed; sentences matching attack
patterns (instruction override, fake system message, approval bypass, action execution, data
exfiltration, false certification, supplier promotion, policy tampering, concealment) are replaced
with `[removed: suspected embedded instruction]` **before indexing**, and a `SecurityFinding` is
returned with every search that hits that document. A negated clause ("Do not create a purchase
order without approval") is not flagged.

**Bounded contract agent.** `create_agent` with three read-only tools, an allowlist check that
refuses procurement write tools, a system prompt with the precedence and untrusted-data rules, and
`response_format=ContractAnalysis` so every finding has `source_id`, `version` and
`effective_date`. `analyze_contracts()` produces the same structure without an LLM for the
workflow and tests.

## 5. Running it

```bash
uv sync --all-groups
uv run pytest tests/evidence                 # needs support-data (see below), no database
uv run ruff check . && uv run ruff format --check .

uv run python scripts/index_documents.py --backend all      # DATABASE_URL + CHROMA_HOST set
uv run python scripts/benchmark_retrieval.py                # writes docs/evidence/retrieval_benchmark.md
uv run python scripts/benchmark_retrieval.py --offline      # in-memory stand-ins
uv run python -m supplyguard.guardrails.red_team support-data docs/evidence/red_team_findings.md
```

`EvidenceService.from_support_data(..., index=False)` (what the workflow runtime uses) still
indexes any document or memory chunk that is missing from the store, so a fresh database
is filled on first start. Stored chunks are never rewritten that way; run
`scripts/index_documents.py` (or `index=True`) to refresh changed documents.

Check the deployed pgvector and ChromaDB services with the supplied documents (indexes
them, then checks scopes inside the database, injection flagging at the workflow boundary,
and the memory store):

```powershell
docker compose --env-file .env up -d --wait postgres chroma
$env:RUN_INTEGRATION="1"; uv run pytest tests/integration/test_evidence_stores.py -v
```

`support-data/` is not committed (COLLABORATION.md). Put your copy at the repository root or
set `SUPPORT_DATA_DIR=/path/to/support-data` (the same variable the workflow reads). Without it, the data-dependent tests are
skipped so CI stays green; the pure guardrail tests still run.

Dependencies needed (to be added to `pyproject.toml` in a shared-file PR): `numpy`,
`psycopg[binary]`, `chromadb`, `langchain`, `langchain-core`; optional `langchain-openai` /
`langchain-huggingface`. Environment variables (to be added to `.env.example`): `EMBEDDINGS_PROVIDER`
(`hash` | `openai` | `huggingface`), `EMBEDDINGS_MODEL`, `CHROMA_PATH`; already there:
`DATABASE_URL`, `CHROMA_HOST`, `CHROMA_PORT`. The compose file already uses `pgvector/pgvector:pg17` and a `chroma` service.

## 6. Acceptance tests → where they are

| Requirement | Test |
|---|---|
| Atlas contract v3 wins over v2 | `test_atlas_contract_v3_wins_over_v2`, `test_v3_ranks_above_v2_in_search` |
| Borealis returned with expired certificate | `test_borealis_returned_with_expired_certificate` |
| Source conflicts are preserved | `test_source_conflicts_are_preserved`, `test_formal_notice_is_baseline_and_email_is_sensitivity` |
| Unauthorized finance documents never reach the model | `test_unauthorized_finance_documents_never_reach_the_model`, `test_confidential_extraction_query_returns_no_finance_document` |
| Prompt injection detected, ignored and reported | `test_prompt_injection_detected_ignored_and_reported`, `test_red_team.py` |
| Approved memories stored separately | `test_approved_memories_are_stored_separately`, `test_new_approved_memory_is_indexed_and_searchable` |
| Every claim has source, version, effective date | `test_every_claim_has_source_version_and_effective_date`, `test_structured_findings_have_citations`, `test_every_item_has_provenance_and_a_valid_score` |
| Returns the shared `EvidenceBundle` contract | `test_contract_boundary.py` |
| Comparable benchmark metrics for all three | `scripts/benchmark_retrieval.py` (same queries, same user, same metrics) |

## 7. Limitations (for the project's limitations section)

- Fact extraction uses regular expressions tuned to the supplied document formats; a new contract
  template would need new patterns.
- The injection detector is pattern based; paraphrased or translated attacks can get through.
  It is one of four layers (sanitizing, protected facts, read-only agent tools, approval in code).
- The default `hash` embedder measures word overlap, not meaning. Use `openai` or `huggingface`
  embeddings for the final benchmark run.
- The in-memory BM25 index is rebuilt at startup (fine for 212 documents, not for millions).
- The shared `EvidenceConflict` carries values only as text; a structured field is proposed.
- Approved incident memories have no effective date unless the workflow passes `approved_on`.
