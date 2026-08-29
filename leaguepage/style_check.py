"""Mechanical style checks for league prose.

Catches what regex can catch from the my-writing-style skill's don'ts:
em/en dashes, the obvious shapes of the negated-parallel contrast family,
generic AI sportswriting, and corporate/LLM vocabulary. These are WARNINGS —
the authoritative sweep is Claude reading the skill and checking the draft;
regex cannot fully enforce voice, only catch the mechanical slips.
"""
from __future__ import annotations

import re

DASH_RE = re.compile(r"[—–]")

NEGATED_PARALLEL_RES = [
    # "not just/only/merely X but Y", "no longer X but Y"
    re.compile(r"\bnot\s+(?:just|only|merely|simply)\b[^.!?;]{0,60}\bbut\b", re.I),
    # "not X, but Y"
    re.compile(r"\bnot\s+[^.!?;,]{1,45},\s*but\b", re.I),
    # "isn't X, it's Y" and contracted/uncontracted variants
    re.compile(r"\b(?:isn't|aren't|wasn't|weren't|is not|are not|was not|were not)\b"
               r"[^.!?;]{0,50}[,;]\s*(?:it's|it is|he's|she's|they're|that's|this is)\b", re.I),
    # "it's not X; it's Y" / "it's not X. It's Y."
    re.compile(r"\bit'?s\s+not\b[^.!?;]{1,60}[.;,]\s*it'?s\b", re.I),
    # a sentence whose whole punch is ending on a bare negation of the prior
    # parallel: "... is worth keeping. The full day is not."
    re.compile(r"\b(?:is|are|was|were|does|do|did|will|would|can|could|has|have|had)\s+not\s*[.!]", re.I),
]

BANNED_PHRASES = [
    # generic AI sportswriting
    "should be an exciting", "only time will tell", "will be looking to",
    "it all comes down to", "keys to victory", "statement game",
    "punched their ticket", "must-win territory", "look to bounce back",
    "leave it all on the field", "when the dust settles", "at the end of the day",
    # corporate / LLM vocabulary (from the skill's don'ts)
    "delve", "circle back", "underscore", "testament to",
    "navigate the complexities", "fast-paced world",
    # quieter writerly tells
    "load-bearing",
]

QUIET_ADVERB_RE = re.compile(r"\bquietly\s+(?:\w+ing|\w+ed|became|builds?|leads?)\b", re.I)


def check_text(text: str) -> list[dict]:
    """Return [{line, kind, excerpt}] warnings for league prose."""
    warnings: list[dict] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith(("<!--", "#", "```", "|")):
            # headings/comments/tables can carry legitimate dashes; the prose is the target
            if DASH_RE.search(stripped) and not stripped.startswith(("<!--", "```")):
                pass  # still check headings below
            else:
                continue
        for m in DASH_RE.finditer(line):
            warnings.append({"line": lineno, "kind": "em-dash",
                             "excerpt": line[max(0, m.start() - 30):m.end() + 30].strip()})
        for rex in NEGATED_PARALLEL_RES:
            m = rex.search(line)
            if m:
                warnings.append({"line": lineno, "kind": "negated-parallel",
                                 "excerpt": m.group(0)[:90]})
        lower = line.lower()
        for phrase in BANNED_PHRASES:
            if phrase in lower:
                warnings.append({"line": lineno, "kind": "banned-phrase",
                                 "excerpt": phrase})
        m = QUIET_ADVERB_RE.search(line)
        if m:
            warnings.append({"line": lineno, "kind": "writerly-tell", "excerpt": m.group(0)})
    return warnings


def format_report(warnings: list[dict]) -> str:
    if not warnings:
        return "style check: no mechanical issues found (the skill-level sweep still applies)."
    lines = [f"style check: {len(warnings)} warning(s) — these are flags, not verdicts:"]
    for w in warnings:
        lines.append(f"  line {w['line']:>4} [{w['kind']}] {w['excerpt']}")
    lines.append("Fix per .claude/skills/my-writing-style/SKILL.md (contrast-by-substitution "
                 "replaces negated parallels; spaced hyphen or semicolon replaces dashes).")
    return "\n".join(lines)
