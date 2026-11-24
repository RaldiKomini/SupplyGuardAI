"""Guardrail for document METADATA (front matter, memory records).

Metadata is as untrusted as the body: a title, a supplier ID or a strategy name ends up
in the provenance header of every chunk and in EvidenceItem.title, so it reaches the
model. Two simple rules:

1. Free-text fields (e.g. title, strategy) get the same injection scan as the body:
   suspicious sentences are removed and reported.
2. Identifier fields (source_id, doc_type, supplier_id, access_scope, status ...) must
   look like identifiers: letters, digits, '-', '_' or '.', at most 64 characters.
   Anything else is not a valid ID, so it is replaced and reported. For access_scope
   this means "no scope", so the document fails closed: nobody can retrieve it.
3. The version must be a positive whole number. Anything else (e.g. "draft") is reported
   and read as version 1. A contract with an invalid version can never supersede another
   contract, and is never treated as the version in force (see ingestion.mark_superseded).
"""

from __future__ import annotations

import re

from supplyguard.guardrails.injection import scan_and_sanitize
from supplyguard.retrieval.models import ContentFinding

SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}$")

DOCUMENT_FREE_TEXT = ("title",)
DOCUMENT_IDENTIFIERS = ("source_id", "doc_type", "supplier_id", "access_scope", "status")
MEMORY_FREE_TEXT = ("strategy",)
MEMORY_IDENTIFIERS = ("memory_id", "case_id", "component_id", "supplier_id", "access_scope")


def clean_metadata(
    meta: dict, source_id: str, free_text: tuple[str, ...], identifiers: tuple[str, ...]
) -> tuple[dict, list[ContentFinding]]:
    """Return a cleaned copy of `meta` and the findings. The input dict is not changed."""
    cleaned = dict(meta)
    findings: list[ContentFinding] = []

    for field in free_text:
        value = str(cleaned.get(field) or "")
        if value:
            safe_value, field_findings = scan_and_sanitize(value, source_id)
            cleaned[field] = safe_value
            findings += field_findings

    if "version" in cleaned and not is_valid_version(cleaned["version"]):
        findings.append(
            ContentFinding(
                source_id=source_id,
                category="invalid_metadata",
                severity="medium",
                snippet=f"version: {str(cleaned['version'])[:60]}",
                action_taken="invalid 'version' read as 1",
            )
        )
        cleaned["version"] = 1

    for field in identifiers:
        value = str(cleaned.get(field) or "").strip()
        if value and not SAFE_IDENTIFIER.match(value):
            cleaned[field] = ""
            findings.append(
                ContentFinding(
                    source_id=source_id,
                    category="invalid_metadata",
                    severity="high",
                    snippet=f"{field}: {value[:60]}",
                    action_taken=f"invalid '{field}' value removed",
                )
            )
    return cleaned, findings


def is_valid_version(value) -> bool:
    """True for 1, 2, 3 ... or "1", "2", "3" ... and False for anything else."""
    text = str(value).strip()
    return text.isdigit() and int(text) >= 1


def safe_version(value) -> int:
    """Version numbers must be positive integers; anything else counts as version 1."""
    try:
        return max(int(value), 1)
    except (TypeError, ValueError):
        return 1
