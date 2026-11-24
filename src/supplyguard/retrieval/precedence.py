"""Source precedence (DOC-PRO-02 and policies.json).

Order of authority:
  1. signed contract, latest version in force
  2. supplier qualification record
  3. formal supplier notice
  4. supplier e-mail
  5. informal chat

Procedures, the finance sheet and incident reports are not in that list. We give
procedures and finance the same weight as qualification records (they are
official internal policy), and incident reports the weight of an e-mail (they
describe the past, not current terms).
"""

from __future__ import annotations

from supplyguard.retrieval.models import Citation

AUTHORITY_RANK = {
    "contract": 1,
    "qualification": 2,
    "procedure": 2,
    "finance": 2,
    "supplier_notice": 3,
    "approved_incident_memory": 4,
    "incident_report": 4,
    "email": 4,
    "chat": 5,
}

# Some facts may only come from specific document types. A supplier notice or an
# e-mail can never change a certification status, a spending cap or an approval rule,
# no matter what it says. This is how "retrieved content cannot modify protected
# values" is enforced in code rather than in a prompt.
PROTECTED_FACT_SOURCES = {
    "certification_status": {"qualification"},
    "spending_cap_eur": {"procedure"},
    "approval_rule": {"procedure"},
}


def authority_rank(doc_type: str) -> int:
    return AUTHORITY_RANK.get(doc_type, 5)


# Ranking adjustments, added to the relevance score (RRF) of a search result.
# They are deliberately SMALL: relevance decides first, authority breaks near-ties.
# For scale: being ranked #1 by one search method is worth 1/61 = 0.016.
AUTHORITY_BONUS_PER_LEVEL = 0.0005  # contract +0.002 ... informal chat +0.0
SUPERSEDED_PENALTY = 0.008  # about half of a #1 ranking: old versions sink below current ones


def ranking_adjustment(doc_type: str, status: str) -> float:
    """Bonus for authoritative sources, penalty for superseded versions.
    Superseded documents are kept (so we can show what changed) but ranked lower."""
    bonus = AUTHORITY_BONUS_PER_LEVEL * (5 - authority_rank(doc_type))
    if status == "superseded":
        bonus -= SUPERSEDED_PENALTY
    return bonus


def precedence_key(c: Citation) -> tuple:
    """Sort key: best source first. Current before superseded, then authority,
    then newest effective date, then highest version."""
    return (
        c.status == "superseded",
        authority_rank(c.doc_type),
        -(c.effective_date.toordinal() if c.effective_date else 0),
        -c.version,
    )


def may_provide(fact_name: str, doc_type: str) -> bool:
    allowed = PROTECTED_FACT_SOURCES.get(fact_name)
    return allowed is None or doc_type in allowed
