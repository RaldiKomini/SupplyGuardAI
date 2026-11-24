# ADR 0004 Evidence layer: hybrid retrieval, precedence in code, guardrails before indexing

## Status

Proposed (branch `feat/evidence-retrieval-guardrails`).

## Context

The workflow needs documentary evidence with provenance for each case. The documents contain
superseded versions, conflicting values between contracts and e-mails, one finance-confidential
sheet, and one document with a hidden prompt injection. The evidence must be reproducible,
because recovery options are validated against it.

## Decision

1. **Hybrid retrieval**: BM25 + vector search, combined with Reciprocal Rank Fusion, plus a small
   authority bonus and a penalty for superseded versions. pgvector is the baseline index and
   ChromaDB the alternative; both use the same metadata and the same interface.
2. **Source precedence and fact extraction in deterministic code** (regular expressions and the
   DOC-PRO-02 order), not in an LLM prompt. Certification status, spending caps and approval rules
   can only come from specific document types.
3. **Access scopes filtered inside every store query**, then checked again at the boundary.
4. **Injection guardrail at ingestion**: hidden comments and instruction-like sentences are removed
   before indexing, so they never reach a prompt. Findings are returned with every result.
5. **Internal models + one mapping module** to the shared contracts (`contract_mapping.py`).

## Alternatives considered

- Vector search only: weak on exact IDs (`MC-17`, `PO-2026-9002`) and cannot rank by authority.
- LLM-based conflict resolution: not reproducible, and it reads the untrusted text it must judge.
- Filtering scopes after retrieval only: unauthorized text could already be in memory or logs.
- Detecting injection only at prompt time: the attack text would be stored and could reach
  another prompt path.
- Using the shared contracts internally: they cannot hold the extracted fact values.

## Consequences

- Rankings and conflicts are explainable and testable without a model.
- New document formats need new extraction patterns.
- Pattern-based detection can miss paraphrased attacks; it is one of several layers.
- Conflict values are text in `EvidenceConflict` until a structured field is added (contract PR).

## Owner

George
