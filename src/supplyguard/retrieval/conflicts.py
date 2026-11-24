"""Read key terms out of documents and report where sources disagree.

We use small, readable regular expressions instead of an LLM here, because these
values feed Athanasia's option validation and must be reproducible. The LLM-based
contract agent can explain the result, but the numbers come from here.

Facts we extract:
  lead_time_days, monthly_capacity_units, expedite_fee_pct, delay_days,
  certification_status (+ certificate expiry date)
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date

from supplyguard.retrieval.models import Conflict, DocumentChunk, Fact
from supplyguard.retrieval.precedence import authority_rank, may_provide, precedence_key

NUM = r"(\d[\d,]*(?:\.\d+)?)"

FACT_PATTERNS: dict[str, list[str]] = {
    "lead_time_days": [
        r"lead time[^\d]{0,120}?"
        + NUM
        + r"\s*(?:calendar\s+)?days?",  # "lead time (...): 14 calendar days" / "Lead time 21 days"
        NUM + r"-day lead time",  # "9-day lead time"
    ],
    "monthly_capacity_units": [
        r"monthly capacity[^:]*:\s*" + NUM + r"\s*units",  # contract clause
        NUM + r"\s*units\s*(?:per|/|a)\s*month",  # "1,500 units per month", "2,500 units/month"
    ],
    "expedite_fee_pct": [
        r"expedit[^%]{0,80}?"
        + NUM
        + r"\s*%",  # "Expedite: ... fee 6%" / "expediting ... charge 10%"
    ],
    "delay_days": [
        NUM + r"-day delay",  # "12-day delay"
        r"delay.{0,60}?" + NUM + r"\s*days?",  # "the delay ... might be closer to 16 days"
    ],
}

# A delay is announced in notices and e-mails. Contracts also contain the word
# "delay" ("notify ... any delay exceeding 3 days"), which is a term, not a delay.
DELAY_DOC_TYPES = {"supplier_notice", "email", "chat"}

# Document types that can describe current supplier terms. Procedures and the finance
# sheet are policy, incident reports describe the past: we do not read supplier facts from them.
FACT_DOC_TYPES = {"contract", "qualification", "supplier_notice", "email", "chat"}


def _number(text: str) -> float:
    value = float(text.replace(",", ""))
    return int(value) if value.is_integer() else value


def _body(chunk: DocumentChunk) -> str:
    """Chunk text without the provenance header and without markdown bold."""
    lines = chunk.text.split("\n", 1)
    body = lines[1] if len(lines) > 1 else lines[0]
    return body.replace("**", "")


def extract_facts(chunk: DocumentChunk, as_of: date = date(2026, 10, 19)) -> list[Fact]:
    """Return every fact we can read from one chunk. `as_of` is the scenario date
    (policies.json), used to decide whether a certificate is still valid."""
    c = chunk.citation
    if c.doc_type not in FACT_DOC_TYPES or not c.supplier_id:
        return []
    body = _body(chunk)
    facts: list[Fact] = []

    def add(name, value):
        if may_provide(name, c.doc_type):
            facts.append(
                Fact(
                    supplier_id=c.supplier_id,
                    name=name,
                    value=value,
                    citation=c,
                    authority_rank=authority_rank(c.doc_type),
                )
            )

    for name, patterns in FACT_PATTERNS.items():
        if name == "delay_days" and c.doc_type not in DELAY_DOC_TYPES:
            continue
        for pattern in patterns:
            m = re.search(pattern, body, re.IGNORECASE)
            if m:
                add(name, _number(m.group(1)))
                break

    # Certification: only qualification records may set it (see PROTECTED_FACT_SOURCES).
    if c.doc_type == "qualification":
        expiry = re.search(r"Expiry:\s*(\d{4}-\d{2}-\d{2})", body)
        if expiry:
            expiry_date = date.fromisoformat(expiry.group(1))
            add("certification_status", "valid" if expiry_date >= as_of else "expired")
            add("certificate_expiry", expiry_date.isoformat())
    return facts


def resolve(facts: list[Fact]) -> tuple[dict[str, Fact], list[Conflict]]:
    """For each fact name pick the planning value from the best source; report disagreements."""
    by_name: dict[str, list[Fact]] = defaultdict(list)
    for f in facts:
        by_name[f.name].append(f)

    planning: dict[str, Fact] = {}
    conflicts: list[Conflict] = []
    for name, group in by_name.items():
        group.sort(key=lambda f: precedence_key(f.citation))
        best = group[0]
        planning[name] = best
        others = [f for f in group[1:] if f.value != best.value]
        if not others:
            continue
        alternatives = []
        for f in others:
            use_as = "ignore: superseded" if f.citation.status == "superseded" else "sensitivity"
            alternatives.append({"value": f.value, "source": f.citation, "use_as": use_as})
        conflicts.append(
            Conflict(
                supplier_id=best.supplier_id,
                fact_name=name,
                planning_value=best.value,
                planning_source=best.citation,
                alternatives=alternatives,
                explanation=_explain(name, best, others),
            )
        )
    return planning, conflicts


def _explain(name: str, best: Fact, others: list[Fact]) -> str:
    parts = [
        f"{name}: planning value {best.value} from {best.citation.short()}"
        f" (authority rank {best.authority_rank})."
    ]
    for f in others:
        if f.citation.status == "superseded":
            parts.append(
                f"{f.citation.source_id} says {f.value} but is superseded, so it is not used."
            )
        else:
            parts.append(
                f"{f.citation.source_id} ({f.citation.doc_type}) says {f.value}; lower precedence, "
                f"so it is kept as a sensitivity case, not the baseline."
            )
    if name == "monthly_capacity_units":
        parts.append(
            "DOC-PRO-02: quantities from e-mail never override contractual capacity without "
            "written "
            "confirmation, but a lower stated capacity is a real risk to plan for."
        )
    return " ".join(parts)
