"""One definition of what "private" looks like, for every gate that checks.

There were two independent lists: one in `site_build` guarding the built
site, one in `scripts/audit_repo_privacy.py` guarding the repository. They
disagreed. A Supabase URL or a database URL pasted into a tracked document
passed the repo audit and would only ever have failed at the *site* audit,
which never opens `docs/`. An AWS key was the other way round. A shape that
is private is private wherever it appears, so there is now one list and both
audits read it.

Nothing here quotes a value it finds. A finding names the class and the
offset, because an audit report that prints the secret has published it a
second time.
"""
from __future__ import annotations

import re
from functools import lru_cache

# Literal strings this codebase writes on purpose and must never ship.
ALWAYS_FORBIDDEN = [
    "ROUGH DRAFT - COMMISSIONER EDIT REQUIRED", "TEST DRAFT", "provisional label",
    "sleeper:pick:", "sleeper:roster:", "sleeper:matchup:", "sleeper:transaction:",
    "editorial:manager:", "editorial:coalition:", "computed:", "archive:issue:",
    "AUTHORING", "commissioner_notes", "REVIEW_PACKET",
    "C:/Users", "C:\\Users", "League-Page-PRIVATE",
]

# Shapes that are private wherever they appear, whatever the surrounding text
# says. The literal list above catches things written on purpose; these catch
# things that would only ever be written by accident, which is the more
# dangerous half.
PRIVATE_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"https?://[A-Za-z0-9-]+\.supabase\.(co|in)"), "Supabase project URL"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ"), "JWT"),
    (re.compile(r"\b(sb|sbp)_[A-Za-z0-9]{20,}"), "Supabase key"),
    # sb_publishable_ / sb_secret_ carry an underscore the pattern above
    # rejects, so they were slipping past a rule written for them.
    (re.compile(r"\bsb_(publishable|secret)_[A-Za-z0-9_-]{10,}"), "Supabase key"),
    (re.compile(r"\bpostgres(?:ql)?://[^\s<>]+"), "database URL"),
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"), "GitHub token"),
    (re.compile(r"sk-ant-[A-Za-z0-9-]{10,}"), "Anthropic key"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AWS access key"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"), "Slack token"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"), "Google API key"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "private key"),
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "email address"),
    (re.compile(r"\b(recommended_status|sleeper_user_id|evidence_id|"
                r"ghost_brief|commissioner_note|quick_note)\b"), "internal field name"),
    (re.compile(r"(?<![\w.])(?:/home/|/Users/)[^\s<>)]+"), "absolute path"),
    (re.compile(r"\b(?:editorial|published|data|migrations|backups|logs)/"
                r"[^\s<>]*\.(?:md|json|sqlite3|sql|db|log)\b"), "private repo path"),
    (re.compile(r"\bPREP\.md\b|\bAUTHORING-[a-z-]+\.md\b"), "authoring artifact"),
]

# Shorter than this and a handle is more likely to be an English word than a
# person, and the scan is a boundary match over every page on the site.
MIN_HANDLE_LEN = 4


@lru_cache(maxsize=512)
def handle_re(handle: str) -> re.Pattern:
    """A private name, matched the way a reader would read it.

    Case-insensitive because a lowercased mention identifies the same person,
    and boundary-anchored because a plain substring test matches a short name
    inside a longer word while missing the same name written in lower case.
    The old test was too loose and too tight at once, and a real 2019 archive
    quote naming a manager by an alias reached production through the gap.
    """
    return re.compile(rf"(?<![A-Za-z0-9]){re.escape(handle)}(?![A-Za-z0-9])",
                      re.IGNORECASE)


def _norm(text: str) -> str:
    return "".join(ch for ch in text.lower() if ch.isalnum())


def _spans(name: str) -> tuple[str, set[int], set[int]]:
    """A public name normalised, plus where each of its words starts and ends."""
    norm, starts, ends = "", set(), set()
    for word in name.split():
        w = _norm(word)
        if not w:
            continue
        starts.add(len(norm))
        norm += w
        ends.add(len(norm))
    return norm, starts, ends


def published_matcher(public_names: list[str]):
    """Whether a candidate name is one the manager already published himself.

    Half of every manager's aliases are not private: they put their own
    nicknames in their team names, so "McLovin" is both an alias and part of
    "Statistical Anomalies (McLovin)". Scanning aliases without subtracting
    the public names flagged 103 violations on a clean build.

    The subtraction has to be careful in the other direction too. Testing
    plain containment against all the names joined together dropped any alias
    that happened to sit inside some *other* team's name -- eleven candidates
    on live data, six of them first-name shaped, silently unscanned across
    the whole site. So a candidate counts as published only when it lines up
    with word boundaries: a whole word, or a run of whole words, because an
    alias is often the slugified team name.
    """
    spans = [_spans(n) for n in public_names]

    def is_published(candidate: str) -> bool:
        c = _norm(candidate)
        if not c:
            return True
        for norm, starts, ends in spans:
            at = norm.find(c)
            while at != -1:
                if at in starts and at + len(c) in ends:
                    return True
                at = norm.find(c, at + 1)
        return False

    return is_published
