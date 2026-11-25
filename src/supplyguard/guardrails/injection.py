"""Prompt-injection detection for untrusted content (documents, e-mails, web, tool output).

Rule (from procedure DOC-PRO-11): text inside retrieved content is DATA. It has no
authority over the assistant, policies or approvals. Instructions found there are
ignored and reported as a security finding.

How it works (simple on purpose, so we can explain every decision):
1. Hidden HTML comments (<!-- ... -->) are always removed: a reader never sees them,
   so a legitimate document has no reason to address an AI inside one.
2. A list of regex patterns, grouped by attack category, is checked.
3. Sentences that match are replaced with a placeholder before the text is indexed
   or placed in a prompt, and a ContentFinding is recorded for each match.

Regexes will not catch every attack. That is why this is only one layer: the
contract agent has no write tools, approval is enforced by Athanasia's code, and
access scopes are filtered before anything reaches the model.
"""

from __future__ import annotations

import re

from supplyguard.retrieval.models import ContentFinding

HIDDEN_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)

# category -> (severity, list of patterns). Patterns are case-insensitive.
PATTERNS: dict[str, tuple[str, list[str]]] = {
    "instruction_override": (
        "high",
        [
            r"\bignore\s+(all\s+|any\s+)?(of\s+)?(the\s+|your\s+)?(previous|prior|above|earlier|preceding)\s+(instructions|policies|rules|prompts?)",
            r"\bdisregard\s+(all\s+|any\s+)?(the\s+|your\s+)?(previous|prior|above|earlier)\b",
            r"\bforget\s+(all\s+|your\s+)?(previous\s+)?instructions\b",
            r"\bnew\s+instructions?\s*:",
        ],
    ),
    "system_impersonation": (
        "high",
        [
            r"\b(system|developer|admin)\s+(instruction|prompt|message|override)s?\b",
            r"(^|[\s'\"(\[])(system|assistant)\s*:",
            r"\bfor\s+ai\s+(assistants?|agents?|models?)\b",
            r"\byou\s+are\s+now\b",
        ],
    ),
    "approval_bypass": (
        "high",
        [
            r"\b(skip|bypass|waive|without|no\s+need\s+for)\s+(the\s+|any\s+)?(human\s+)?(approval|sign-?off|review)\b",
            r"\bauto-?approve\b",
            r"\bapprove\s+(any|all|every)\s+(purchase|order)s?\b",
        ],
    ),
    "action_execution": (
        "high",
        [
            r"\b(create|execute|place|submit|issue)\b[^.]{0,40}\bpurchase\s+orders?\b",
        ],
    ),
    "data_exfiltration": (
        "high",
        [
            r"\b(reveal|show|print|dump|leak|disclose|send|export)\b[^.]{0,40}\b(confidential|margin|password|secret|system\s+prompt|api\s+key)",
        ],
    ),
    "false_certification": (
        "high",
        [
            r"\b(mark|treat|consider|list|record)\b[^.]{0,40}\bas\s+(certified|approved|qualified|iso[\s-]?9001)",
            r"\bcertification\s+(status\s+)?(is|should\s+be)\s+(now\s+)?(valid|approved)\b",
        ],
    ),
    "supplier_promotion": (
        "medium",
        [
            r"\b(the\s+)?(best|preferred|only)\s+supplier\b",
            r"\b(always|only)\s+(buy|order|purchase)\s+from\b",
        ],
    ),
    "policy_tampering": (
        "high",
        [
            r"\b(change|set|modify|override|raise|remove)\s+(the\s+)?(spending\s+(cap|limit)|approval\s+(tiers?|rules?|requirements?)|access\s+scopes?)\b",
        ],
    ),
    "concealment": (
        "medium",
        [
            r"\bdo\s+not\s+(mention|tell|reveal|disclose)\s+(this|it)\b",
            r"\bkeep\s+this\s+(secret|hidden)\b",
        ],
    ),
}

# Ignore a match if its clause is negated, e.g. the user's own
# "Do not create a purchase order without approval" is a safety rule, not an attack.
# We only look back to the start of the clause and at most 40 characters.
NEGATION = re.compile(
    r"\b(do\s+not|don't|never|must\s+not|may\s+not|cannot|should\s+not)\b", re.IGNORECASE
)
CLAUSE_END = re.compile(r"[.!?;:\n]")

COMPILED = {
    cat: (sev, [re.compile(p, re.IGNORECASE | re.MULTILINE) for p in pats])
    for cat, (sev, pats) in PATTERNS.items()
}

PLACEHOLDER = "[removed: suspected embedded instruction]"
HIDDEN_PLACEHOLDER = "[removed: hidden content]"


def _is_negated(text: str, start: int) -> bool:
    window = text[max(0, start - 40) : start]
    boundaries = list(CLAUSE_END.finditer(window))
    clause = window[boundaries[-1].end() :] if boundaries else window
    return bool(NEGATION.search(clause))


def detect_injection(text: str, source_id: str = "user_input") -> list[ContentFinding]:
    """Return one finding per (category, match). Empty list = nothing suspicious."""
    findings: list[ContentFinding] = []
    seen: set[tuple[str, str]] = set()
    for category, (severity, regexes) in COMPILED.items():
        for rx in regexes:
            for m in rx.finditer(text):
                if _is_negated(text, m.start()):
                    continue
                snippet = m.group(0).strip()[:80]
                if (category, snippet.lower()) in seen:
                    continue
                seen.add((category, snippet.lower()))
                findings.append(
                    ContentFinding(
                        source_id=source_id, category=category, severity=severity, snippet=snippet
                    )
                )
    # A hidden comment is itself a finding, even if no pattern matched inside it.
    for m in HIDDEN_COMMENT.finditer(text):
        findings.append(
            ContentFinding(
                source_id=source_id,
                category="hidden_content",
                severity="medium",
                snippet=m.group(0)[:80],
            )
        )
    return findings


def sanitize(text: str) -> str:
    """Remove hidden comments and every sentence that contains an injection pattern."""
    text = HIDDEN_COMMENT.sub(HIDDEN_PLACEHOLDER, text)
    clean_lines = []
    for line in text.splitlines():
        sentences = re.split(r"(?<=[.!?])\s+", line)
        sentences = [PLACEHOLDER if s and detect_injection(s) else s for s in sentences]
        clean_lines.append(" ".join(sentences))
    return "\n".join(clean_lines)


# What happens to an instruction found by scan_text(): the text is NOT changed (it may be the
# user's own message), but the instruction is reported and never obeyed. Documents are
# different: scan_and_sanitize() removes the instruction before indexing.
REPORTED_NOT_OBEYED = "reported and treated as data, never obeyed (the text itself was not changed)"


def scan_text(text: str, source_id: str) -> list[ContentFinding]:
    """Findings for text we do not store or rewrite, e.g. a user's message or a pasted notice."""
    return [
        f.model_copy(update={"action_taken": REPORTED_NOT_OBEYED})
        for f in detect_injection(text, source_id)
    ]


def scan_and_sanitize(text: str, source_id: str) -> tuple[str, list[ContentFinding]]:
    """Convenience: findings are computed on the ORIGINAL text, sanitized text is what we keep."""
    findings = detect_injection(text, source_id)
    return (sanitize(text) if findings else text), findings


# Severity of categories that are not regex patterns.
OTHER_SEVERITIES = {"hidden_content": "medium", "invalid_metadata": "high"}
RESTORED_SNIPPET = "(restored from the stored security flags)"


def findings_from_flags(source_id: str, flags: list[str]) -> list[ContentFinding]:
    """Rebuild findings from the category names stored with a chunk (`security_flags`).

    Used after a restart: the vector store keeps the sanitized text AND these flags, but
    the detailed findings were only in memory. The attack text itself is never stored,
    so the restored findings carry the category and severity, not the original snippet."""
    severities = {cat: sev for cat, (sev, _) in PATTERNS.items()} | OTHER_SEVERITIES
    return [
        ContentFinding(
            source_id=source_id,
            category=flag,
            severity=severities.get(flag, "high"),
            snippet=RESTORED_SNIPPET,
        )
        for flag in sorted(set(flags))
    ]
