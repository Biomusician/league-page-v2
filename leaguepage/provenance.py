"""Who wrote what a reader sees, and how we know.

Three different facts, kept apart:

  1. ORIGIN     -- who supplied the wording in the first place: a language
                   model, our own deterministic code, or the Commissioner.
  2. EDITED     -- whether the Commissioner has changed a generated text
                   since it was accepted. Exact equality by hash, nothing
                   softer: one edited character is an edit.
  3. ASSISTANCE -- whether AI materially helped the research or writing
                   behind Commissioner-authored prose.

Origin is durable. The Commissioner rewriting most of a generated section
does not make it his in origin; it makes it a generated section he edited,
and the public label says exactly that. The only way origin changes is a
deliberate workflow act ("Replace with my copy"), never a similarity score.

Nothing here infers from how the writing sounds. Every state is recorded
when a workflow action happens: accepting a proposal, resetting to a
generated draft, a Claude draft arriving under the ROUGH DRAFT contract,
the Commissioner writing into an empty section. Where nothing was
recorded, the answer is "unknown" and the page carries no label, because
a guessed label is a false statement about who wrote something.

Nothing here can leak either. `method` is a key into a fixed table of
input-class descriptions rather than free text; every public sentence is
assembled from this module's own vocabulary; the generated baseline kept
for the edit metric is private and never enters a snapshot.
"""
from __future__ import annotations

import difflib
import hashlib
import re

from leaguepage.storage import Storage

# The providers we are willing to name. A generator we cannot identify is
# reported as unknown; it never guesses, because a wrong badge is a false
# statement about who wrote something.
GENERATORS = {
    "claude-code": "Claude Code",
    "chatgpt": "ChatGPT",
}

# Not a provider: a marker meaning "our own deterministic code composed this
# from computed results". No model was involved, and calling it AI would be
# as false as the badge it replaced.
DETERMINISTIC = "deterministic"

# What the generator was working from, described as a class of input. These
# are the only strings that can ever reach a reader, which is what makes the
# privacy guarantee structural rather than a review step.
METHODS = {
    "matchup-brief": ("from synced Sleeper league data and the structured "
                      "matchup brief"),
    "section-brief": ("from synced Sleeper league data and the section's "
                      "editorial brief"),
    "transactions": ("from synced Sleeper league data and deterministic "
                     "transaction analysis"),
    "draft-data": "from the league's synced draft results and reference ranks",
    "weekly-awards": ("from the week's computed award results as decided on "
                      "the Commissioner's Desk"),
    "model-board": ("from synced Sleeper league data, results to date and "
                    "reference ranks"),
    "roster-analysis": ("from synced Sleeper transactions and deterministic "
                        "roster analysis"),
    "team-brief": ("from synced Sleeper league data, results to date and the "
                   "remaining schedule"),
}

# Sections whose heading carries no line of its own because the parts
# inside them each carry one: Common Tactical Picture is exactly its
# matchup previews plus an optional opening, and a single badge over all of
# them would describe none of them.
SECTION_LEVEL_SILENT = {"ctp"}

# Who supplied the wording. "unknown" exists so a row can carry an
# assistance fact before an origin is established; it never labels a page.
ORIGINS = ("ai", "deterministic", "commissioner", "unknown")

# Whether AI materially helped Commissioner-authored prose. Deterministic
# packets (Sleeper arithmetic, reference ranks, a database query) are not
# AI, so "deterministic-analysis" is recorded for completeness and reads,
# publicly, as plain Commissioner writing.
ASSISTANCE = ("none", "ai-writing", "ai-research", "deterministic-analysis")
AI_ASSISTANCE = {"ai-writing", "ai-research"}

# The workflow act that set the row. Private; useful in an audit.
EVENTS = ("proposal-accept", "reset-generated", "marker-arrival",
          "commissioner-save", "replace-with-my-copy", "rankings-note",
          "assistance", "backfill")

UNKNOWN_GENERATOR = "an AI assistant (provider not recorded)"
UNKNOWN_METHOD = "Generation method not recorded."
NO_EDITS = "No Commissioner edits."

# The six public labels. Fixed strings: the template prints these and only
# these, and a test pins that the module can produce nothing else.
LABEL_AI = "AI-generated"
LABEL_AI_EDITED = "AI-generated · Commish edited"
LABEL_AUTO = "Automatically generated"
LABEL_AUTO_EDITED = "Automatically generated · Commish edited"
LABEL_COMMISH = "Commish-written"
LABEL_COMMISH_AI = "Commish-written · AI-assisted"
LABELS = (LABEL_AI, LABEL_AI_EDITED, LABEL_AUTO, LABEL_AUTO_EDITED,
          LABEL_COMMISH, LABEL_COMMISH_AI)
BADGES = {"ai": "AI", "deterministic": "AUTO", "commissioner": "COMMISH"}

_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)


# ------------------------------------------------------------ hashing

def normalise(text: str | None) -> str:
    """The text as prose: HTML comments gone (the ROUGH DRAFT marker and a
    usage tracker are scaffolding, not writing), line endings unified,
    outer whitespace dropped. Removing a comment is not an edit."""
    return _HTML_COMMENT_RE.sub("", (text or "").replace("\r\n", "\n")).strip()


def text_sha(text: str | None) -> str:
    return hashlib.sha256(normalise(text).encode("utf-8")).hexdigest()


# ------------------------------------------------------------ recording

def record(storage: Storage, *, league_slug: str, season: str, issue_key: str,
           section: str, generator: str | None, method: str | None,
           text: str, origin: str | None = None, assistance: str | None = None,
           event: str | None = None) -> None:
    """Remember that this exact text was generated, and by what.

    The baseline is kept privately so the Desk can say roughly how much he
    has changed since. Assistance already on the row survives: accepting a
    proposal is an act about origin, not about what research he read."""
    prior = storage.get_prose_provenance(league_slug, season, issue_key, section) or {}
    gen = generator if (generator in GENERATORS or generator == DETERMINISTIC) else None
    origin = origin or (DETERMINISTIC if gen == DETERMINISTIC else "ai")
    if origin not in ORIGINS:
        raise ValueError(f"unknown origin {origin!r}")
    storage.set_prose_provenance(
        league_slug=league_slug, season=season, issue_key=issue_key,
        section=section, generator=gen,
        method=method if method in METHODS else None,
        generated_sha=text_sha(text),
        origin=origin,
        assistance=_pick_assistance(prior.get("assistance"), assistance),
        baseline_text=normalise(text),
        event=event if event in EVENTS else None)


def mark_commissioner(storage: Storage, *, league_slug: str, season: str,
                      issue_key: str, section: str, assistance: str | None = None,
                      method: str | None = None, event: str = "commissioner-save") -> None:
    """The Commissioner supplied the wording. No baseline: there is nothing
    generated to compare his text with."""
    prior = storage.get_prose_provenance(league_slug, season, issue_key, section) or {}
    storage.set_prose_provenance(
        league_slug=league_slug, season=season, issue_key=issue_key,
        section=section, generator=None,
        method=method if method in METHODS else prior.get("method"),
        generated_sha="", origin="commissioner",
        assistance=_pick_assistance(prior.get("assistance"), assistance),
        baseline_text=None, event=event if event in EVENTS else None)


def note_assistance(storage: Storage, *, league_slug: str, season: str,
                    issue_key: str, section: str, kind: str = "ai-writing",
                    method: str | None = None) -> None:
    """AI help reached this section: a proposal he read, a rough draft
    beside the box he wrote in. Origin is untouched; a row with no origin
    yet is created as unknown so the fact is not lost."""
    if kind not in ASSISTANCE:
        raise ValueError(f"unknown assistance {kind!r}")
    prior = storage.get_prose_provenance(league_slug, season, issue_key, section)
    if prior:
        storage.set_prose_assistance(
            league_slug=league_slug, season=season, issue_key=issue_key,
            section=section, assistance=_pick_assistance(prior.get("assistance"), kind))
        return
    storage.set_prose_provenance(
        league_slug=league_slug, season=season, issue_key=issue_key,
        section=section, generator=None, method=method if method in METHODS else None,
        generated_sha="", origin="unknown", assistance=kind, baseline_text=None,
        event="assistance")


def _pick_assistance(prior: str | None, new: str | None) -> str:
    """AI assistance, once recorded, is not forgotten by a later act that
    said nothing about it."""
    if new in AI_ASSISTANCE:
        return new
    if prior in AI_ASSISTANCE:
        return prior
    return new if new in ASSISTANCE else (prior if prior in ASSISTANCE else "none")


def note_rankings(storage: Storage, *, league_slug: str, season: str,
                  label: str, entries: list[dict]) -> None:
    """Peer and Near-Peer's prose is the notes he types beside his ranking.
    A saved ranking with at least one note is Commissioner-written prose
    for that issue; the ranks themselves are data and claim nothing."""
    if not any((e.get("note") or "").strip() for e in entries):
        return
    issue_key = "draft" if label == "preseason" else label
    mark_commissioner(storage, league_slug=league_slug, season=season,
                      issue_key=issue_key, section="power", event="rankings-note")


def forget(storage: Storage, *, league_slug: str, season: str, issue_key: str,
           section: str) -> None:
    storage.clear_prose_provenance(league_slug=league_slug, season=season,
                                   issue_key=issue_key, section=section)


# ------------------------------------------------------------ classifying

def origin_of(row: dict | None) -> str:
    """Rows written before origin existed carried only a generator and a
    hash; they read the way they always did."""
    if not row:
        return "unknown"
    origin = row.get("origin")
    if origin in ORIGINS:
        return origin
    if row.get("generator") == DETERMINISTIC:
        return "deterministic"
    return "ai" if row.get("generated_sha") else "unknown"


def classify(row: dict | None, text: str | None) -> dict | None:
    """The public statement about this text, or None when nothing honest
    can be said. Never contains the baseline, a percentage, or free text."""
    origin = origin_of(row)
    if origin == "unknown":
        return None
    assistance = (row or {}).get("assistance") or "none"
    if origin == "commissioner":
        return _public("commissioner", "commissioner", None,
                       (row or {}).get("method"), assistance)
    exact = bool(row.get("generated_sha")) and text_sha(text) == row["generated_sha"]
    return _public(origin, "exact" if exact else "edited", row.get("generator"),
                   row.get("method"), assistance)


def _public(origin: str, relationship: str, generator: str | None,
            method: str | None, assistance: str) -> dict:
    gen = generator if generator in GENERATORS else None
    gen_label = GENERATORS.get(gen or "", UNKNOWN_GENERATOR)
    how = METHODS.get(method or "")
    edited = relationship == "edited"
    if origin == "ai":
        label = LABEL_AI_EDITED if edited else LABEL_AI
        if edited:
            detail = (f"Originally generated by {gen_label} {how}; the Commissioner "
                      f"subsequently edited the prose." if how else
                      f"Originally generated by {gen_label}; the Commissioner "
                      f"subsequently edited the prose. {UNKNOWN_METHOD}")
        else:
            detail = (f"Generated by {gen_label} {how}. {NO_EDITS}" if how else
                      f"Generated by {gen_label}. {UNKNOWN_METHOD} {NO_EDITS}")
    elif origin == "deterministic":
        label = LABEL_AUTO_EDITED if edited else LABEL_AUTO
        if edited:
            detail = (f"Produced {how}; the Commissioner subsequently edited the "
                      f"prose." if how else
                      f"Produced by deterministic analysis; the Commissioner "
                      f"subsequently edited the prose. {UNKNOWN_METHOD}")
        else:
            detail = (f"Produced {how}. {NO_EDITS}" if how else
                      f"Produced by deterministic analysis. {UNKNOWN_METHOD} {NO_EDITS}")
    else:
        assisted = assistance in AI_ASSISTANCE
        label = LABEL_COMMISH_AI if assisted else LABEL_COMMISH
        if assisted:
            research = ("AI-assisted matchup research" if method == "matchup-brief"
                        else "AI-assisted editorial research")
            detail = f"Commissioner-written using {research} and synced league data."
        else:
            detail = "Written by the Commissioner."
    return {
        "origin": origin,
        "relationship": relationship,
        "edited": edited,
        "assistance": assistance if assistance in ASSISTANCE else "none",
        "generator": gen,
        "generator_label": gen_label if origin == "ai" else (
            "deterministic analysis" if origin == "deterministic" else "the Commissioner"),
        "method": method if method in METHODS else None,
        "label": label,
        "detail": detail,
        # One sentence for a screen reader, and for the older template shape.
        "caption": f"{label}. {detail}",
        "badge_text": BADGES[origin],
    }


def state_for(storage: Storage, *, league_slug: str, season: str,
              issue_key: str, section: str, text: str | None) -> dict | None:
    """Public provenance for this section's CURRENT text, or None when
    nothing was ever recorded. An edited generated text is still generated
    in origin, and says so."""
    row = storage.get_prose_provenance(league_slug, season, issue_key, section)
    return classify(row, text)


def section_state(storage: Storage, *, league_slug: str, season: str,
                  issue_key: str, section: str, text: str | None) -> dict | None:
    """What a published section's heading says, or None. The matchup
    parent stays silent; its previews speak for themselves inline."""
    if section in SECTION_LEVEL_SILENT:
        return None
    return state_for(storage, league_slug=league_slug, season=season,
                     issue_key=issue_key, section=section, text=text)


def public_shape(prov: dict | None) -> dict | None:
    """A provenance dict from any snapshot vintage, in today's shape. The
    first snapshots stored only a caption and a badge."""
    if not prov:
        return None
    if prov.get("label"):
        return prov
    label = LABEL_AUTO if prov.get("badge_text") == "AUTO" else LABEL_AI
    return {**prov, "label": label, "detail": prov.get("caption") or ""}


def describe(generator: str | None, method: str | None) -> dict:
    """An untouched AI-generated text, described."""
    return _public("ai", "exact", generator, method, "none")


def describe_machine(method: str | None) -> dict:
    """An untouched deterministic text, described. Force Flow's reading of
    the week is arithmetic over synced data, and this says so without a
    provider badge it has not earned."""
    return _public("deterministic", "exact", None, method, "none")


def describe_commissioner(*, assisted: bool = False, method: str | None = None) -> dict:
    return _public("commissioner", "commissioner", None, method,
                   "ai-writing" if assisted else "none")


def for_sections(storage: Storage, league_slug: str, season: str,
                 issue_key: str, sections: list[dict]) -> dict[str, dict]:
    """{section_key: provenance} for every section something is known about."""
    out = {}
    for s in sections:
        key = s.get("module_key") or s.get("section")
        if not key:
            continue
        st = section_state(storage, league_slug=league_slug, season=season,
                           issue_key=issue_key, section=key, text=s.get("content_md"))
        if st:
            out[key] = st
    return out


def inline_html(prov: dict | None) -> str:
    """The provenance line as markup, for a section assembled from parts
    (each matchup preview inside Common Tactical Picture). Same classes and
    role as the template, so one stylesheet rule covers both."""
    if not prov:
        return ""
    return (f'<p class="prov" role="note"><span class="prov-mark" aria-hidden="true">'
            f'{prov["badge_text"]}</span><span class="prov-label">{prov["label"]}</span>'
            f'<span class="prov-detail">{prov["detail"]}</span></p>')


# ------------------------------------------------------------ edit metric
#
# "Changed from generated baseline": a descriptive number for the Desk,
# never an authorship detector. It compares prose tokens, so a Markdown
# marker or a line ending cannot masquerade as a rewrite, and punctuation
# counts a quarter of a word so a comma reads as a small edit rather than
# no edit or a large one.

PUNCT_WEIGHT = 0.25
_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9'’\-]*|[^\sA-Za-z0-9]")
_TAG_RE = re.compile(r"<[^>]+>")
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+", re.M)
_LIST_RE = re.compile(r"^\s*(?:[-*+]|\d+\.)\s+", re.M)
_QUOTE_RE = re.compile(r"^\s*>\s?", re.M)
_EMPHASIS_RE = re.compile(r"[*_`~]+")


def prose_tokens(text: str | None) -> list[tuple[str, float]]:
    t = normalise(text)
    t = _TAG_RE.sub(" ", t)
    t = _HEADING_RE.sub(" ", t)
    t = _LIST_RE.sub(" ", t)
    t = _QUOTE_RE.sub(" ", t)
    t = _EMPHASIS_RE.sub("", t)
    return [(tok, 1.0 if tok[0].isalnum() else PUNCT_WEIGHT)
            for tok in _TOKEN_RE.findall(t)]


def changed_from_baseline(baseline: str | None, current: str | None) -> int:
    """Percent of the prose that differs from the generated baseline, 0-100.

    Both sides are measured: words removed from the baseline and words that
    appear in the current text, and the larger share is reported. A moved
    paragraph counts once, at its own size. Any real change reports at
    least 1; text equivalent after normalisation reports exactly 0."""
    a, b = prose_tokens(baseline), prose_tokens(current)
    if [t for t, _ in a] == [t for t, _ in b]:
        return 0
    total = max(sum(w for _, w in a), sum(w for _, w in b))
    if total <= 0:
        return 0
    matcher = difflib.SequenceMatcher(None, [t for t, _ in a], [t for t, _ in b],
                                      autojunk=False)
    changed_a = changed_b = 0.0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        changed_a += sum(w for _, w in a[i1:i2])
        changed_b += sum(w for _, w in b[j1:j2])
    pct = round(100 * max(changed_a, changed_b) / total)
    return max(1, min(100, pct))


def changed_hint(pct: int) -> str:
    """A reading of the number for the Desk. UX only; it changes no label."""
    if pct <= 0:
        return "exact generated baseline"
    if pct < 20:
        return "lightly edited"
    if pct < 60:
        return "edited"
    return "substantially rewritten"


def desk_line(row: dict | None, text: str | None) -> str:
    """One private line for the Desk card: the public label plus the metric
    where a baseline exists."""
    prov = classify(row, text)
    if prov is None:
        if row and (row.get("assistance") in AI_ASSISTANCE):
            return "Origin not recorded (AI assistance noted)"
        return "Origin not recorded"
    if prov["origin"] in ("ai", "deterministic"):
        kind = "AI-origin draft" if prov["origin"] == "ai" else "Generated copy"
        if prov["edited"] and row.get("baseline_text") is not None:
            pct = changed_from_baseline(row["baseline_text"], text)
            return f"{kind} · ~{pct}% changed from generated baseline · {changed_hint(pct)}"
        if prov["edited"]:
            return f"{kind} · edited (no baseline kept to measure by)"
        return f"{kind} · exact generated baseline"
    return prov["label"]
