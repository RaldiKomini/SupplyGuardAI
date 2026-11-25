"""Access-scope filtering.

Elpiniki's application layer authenticates the user and produces an
AuthorizationContext with the user's allowed document scopes. We only consume
those scopes: we do not decide who the user is.

Rule (DOC-PRO-10): users may only retrieve documents within their granted scopes,
and unauthorized documents must be removed BEFORE anything is placed in the
model context. We filter in three places (defence in depth):
  1. inside the vector-store query (SQL WHERE / Chroma where-filter),
  2. before lexical (BM25) scoring,
  3. once more on the final result list (enforce_scope), in case a store misbehaves.
"""

from __future__ import annotations

from collections.abc import Iterable

KNOWN_SCOPES = {"public", "internal", "procurement", "finance-confidential"}


def scopes_from(auth_context) -> set[str]:
    """Accept Elpiniki's AuthorizationContext, a dict, or a plain list/set of scopes.

    We look for an attribute or key called `allowed_scopes` (or `document_scopes`).
    Anything we do not recognise gives NO access, never full access.
    """
    if auth_context is None:
        return set()
    if isinstance(auth_context, (set, list, tuple, frozenset)):
        raw = auth_context
    elif isinstance(auth_context, dict):
        raw = auth_context.get("allowed_scopes") or auth_context.get("document_scopes") or []
    else:
        raw = (
            getattr(auth_context, "allowed_scopes", None)
            or getattr(auth_context, "document_scopes", None)
            or []
        )
    if isinstance(raw, str):  # users.csv stores "public;internal;procurement"
        raw = raw.split(";")
    return {s.strip() for s in raw if s and s.strip() in KNOWN_SCOPES}


def is_allowed(access_scope: str, allowed_scopes: set[str]) -> bool:
    """A document with a missing or unknown scope is treated as not allowed."""
    return bool(access_scope) and access_scope in allowed_scopes


def enforce_scope(items: Iterable, allowed_scopes: set[str]) -> tuple[list, int]:
    """Keep only items the user may see. Works on DocumentChunk or Evidence.

    Returns (allowed_items, number_removed). The number is reported; the removed
    titles are not, so nothing confidential leaks through the report.
    """
    kept, removed = [], 0
    for item in items:
        citation = item.citation if hasattr(item, "citation") else item.chunk.citation
        if is_allowed(citation.access_scope, allowed_scopes):
            kept.append(item)
        else:
            removed += 1
    return kept, removed
