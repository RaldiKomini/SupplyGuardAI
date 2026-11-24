"""Decide whether the incident is clear enough to investigate, and what to ask if not.

Plain code, no model: the candidates come from MasterDataService, the choice is made here.
"""

import re

from supplyguard.contracts import ComponentCandidate, SupplierCandidate

MAX_CLARIFICATION_ROUNDS = 2
MAX_OPTIONS_SHOWN = 5
DELAY_QUESTION = "How many days late will the delivery be?"

_COMPONENT_ID = re.compile(r"\bMC[\s-]?(\d+)\b", re.IGNORECASE)


def pick_supplier(
    query: str, candidates: tuple[SupplierCandidate, ...]
) -> SupplierCandidate | None:
    """The supplier the user certainly means, or None if it is unknown or ambiguous."""
    wanted = query.strip().lower()
    for candidate in candidates:
        if wanted in (candidate.supplier_id.lower(), candidate.name.lower()):
            return candidate
    return candidates[0] if len(candidates) == 1 else None


def pick_component(
    query: str, candidates: tuple[ComponentCandidate, ...]
) -> ComponentCandidate | None:
    """Only an explicit ID is accepted. A description ("the motor board") is always confirmed."""
    match = _COMPONENT_ID.search(query)
    if not match:
        return None
    wanted_id = f"MC-{int(match.group(1))}"
    return next((c for c in candidates if c.component_id == wanted_id), None)


def _choices(texts: list[str]) -> str:
    """["A", "B", "C"] -> "A, B or C"."""
    texts = texts[:MAX_OPTIONS_SHOWN]
    return texts[0] if len(texts) == 1 else ", ".join(texts[:-1]) + " or " + texts[-1]


def supplier_question(query: str | None, options: tuple[SupplierCandidate, ...]) -> str:
    names = [f"{s.name} ({s.supplier_id})" for s in options]
    if query and len(options) > 1:
        return f"Which supplier do you mean by '{query}': {_choices(names)}?"
    if query:
        return f"I couldn't find a supplier called '{query}'. Which supplier is it (name or ID)?"
    if options:
        return f"Which supplier is affected? This component is supplied by {_choices(names)}."
    return "Which supplier announced the disruption?"


def component_question(query: str | None, options: tuple[ComponentCandidate, ...]) -> str:
    if query and options:
        names = [f"{c.component_id} ({c.description})" for c in options]
        return f"Which component is '{query}'? Did you mean {_choices(names)}? Please give the ID."
    if query:
        return f"I couldn't find the component '{query}'. Which component ID is affected?"
    return "Which component is affected (e.g. MC-17)?"


def clarification_message(questions: list[str]) -> str:
    """All open questions in one message, so the user answers once."""
    return "Before I investigate, I need to confirm:\n" + "\n".join(f"- {q}" for q in questions)
