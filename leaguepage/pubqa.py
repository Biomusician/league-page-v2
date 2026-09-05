"""Publication quality gate — the pre-publish proofreader.

The Commissioner should not be the final regex. This module reads an issue
the way a copy desk would: does anything here leak an internal identifier,
render as broken markup, still say "pending", contradict the data as it
stands today, or contradict a methodology decision we already made?

Design rules, in order of importance:

1. **Voice is not a defect.** Fragments, jokes, slang, deliberate
   capitalization, Air Force jargon and cheerful abuse of English are the
   product. Every copy check here is mechanical and high-confidence: a
   doubled period, a repeated word, a comma where a full stop belongs.
   Nothing in this module has an opinion about style.
2. **Blockers stop publication; warnings never do.** A warning IS the
   override — the Commissioner reads it and publishes anyway. Blockers are
   things no reader should ever see: an internal roster id, a raw
   placeholder, markup that failed to render, a private handle.
3. **Privacy blockers can never be cleared by a flag.** `privacy=True`
   findings have no override path anywhere in the codebase, deliberately.
4. **Freshness flags, never rewrites.** When the world moved after the prose
   was written, the finding carries BEFORE and AFTER and stops there.
   Commissioner Content is not edited by a machine.

The same checker runs against live editorial state (`check_issue`) and
against an already-frozen snapshot (`check_snapshot`), so the gate that
protects the next issue can also audit the last one.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, asdict


from leaguepage import prose
from leaguepage.config import League
from leaguepage.publish import BLOCKED_MARKERS, strip_editorial_comments
from leaguepage.storage import Storage

# ---------------------------------------------------------------- categories

IDENTITY = "identity"
FORMATTING = "formatting"
PLACEHOLDER = "placeholder"
COPY = "copy"
FRESHNESS = "freshness"
ANALYTICS = "analytics"
PRIVACY = "privacy"
COHERENCE = "coherence"

BLOCKER = "blocker"
WARNING = "warning"

CATEGORY_LABELS = {
    IDENTITY: "Identity",
    FORMATTING: "Formatting",
    PLACEHOLDER: "Placeholder / dead state",
    COPY: "Copy editing",
    FRESHNESS: "Freshness",
    ANALYTICS: "Analytical consistency",
    PRIVACY: "Privacy",
    COHERENCE: "Cross-section coherence",
}
CATEGORY_ORDER = [PRIVACY, IDENTITY, PLACEHOLDER, FORMATTING, FRESHNESS, ANALYTICS,
                  COHERENCE, COPY]


@dataclass
class Finding:
    category: str
    severity: str
    title: str
    detail: str
    module_key: str | None = None
    excerpt: str | None = None       # the offending text, as written
    suggestion: str | None = None    # a mechanical replacement, when one is safe
    evidence: list[str] = field(default_factory=list)
    privacy: bool = False
    # Exact strings for a literal, reviewable Accept: replace fix_from with
    # fix_to in the section source. Both empty when no safe mechanical fix
    # exists, which is most findings.
    fix_from: str | None = None
    fix_to: str | None = None

    @property
    def finding_id(self) -> str:
        raw = f"{self.category}|{self.module_key}|{self.title}|{self.excerpt}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]

    def as_dict(self) -> dict:
        d = asdict(self)
        d["finding_id"] = self.finding_id
        d["category_label"] = CATEGORY_LABELS.get(self.category, self.category)
        return d


# ------------------------------------------------------------------ context


@dataclass
class QAContext:
    """Everything the checks compare prose against. Built once per issue."""
    league_slug: str
    season: str
    issue_key: str
    week: int | None = None
    current_week: int | None = None
    public_names: dict[int, str] = field(default_factory=dict)
    # normalized token sets of the current public names, for heading resolution
    name_tokens: dict[int, set[str]] = field(default_factory=dict)
    team_slugs: set[str] = field(default_factory=set)
    private_handles: list[str] = field(default_factory=list)
    # roster_id -> positional rank map, as it stands right now
    positional_ranks: dict[int, dict[str, int]] = field(default_factory=dict)
    positions_n: int = 0
    # roster_id -> {"drafted": {player names}, "rostered": {player names}}
    rosters: dict[int, dict[str, set[str]]] = field(default_factory=dict)
    # player name -> position, for the K/DST methodology check
    player_positions: dict[str, str] = field(default_factory=dict)
    current_pairings: set[frozenset] = field(default_factory=set)
    n_teams: int = 0
    # --- what the cross-section checks (leaguepage.coherence) compare against
    lineup_format: str | None = None            # "superflex" | "1qb"
    # player name -> {"rid", "position", "status"} for every rostered player
    players: dict[str, dict] = field(default_factory=dict)
    sleeper_names: dict[int, str] = field(default_factory=dict)
    callsigns: dict[str, int] = field(default_factory=dict)
    former_names: dict[str, int] = field(default_factory=dict)
    other_league_paragraphs: set[str] = field(default_factory=set)
    other_league_label: str | None = None


_WORD_RE = re.compile(r"[a-z0-9']+")


def _norm_tokens(text: str) -> set[str]:
    """Token set for loose team-name matching: lowercase words, no emoji,
    no punctuation, and no noise words that carry no identity."""
    toks = {t.strip("'") for t in _WORD_RE.findall(text.lower().replace("’", "'"))}
    return {t for t in toks if t and t not in {"the", "a", "an", "of", "and"}}


def build_context(
    storage: Storage,
    league: League,
    season: str,
    issue_key: str,
    *,
    week: int | None = None,
) -> QAContext:
    from leaguepage.team_names import resolve_public_names

    resolved = resolve_public_names(storage, league)
    public_names = {rid: v["name"] for rid, v in resolved.items() if v["name"]}
    ctx = QAContext(
        league_slug=league.slug, season=season, issue_key=issue_key, week=week,
        public_names=public_names,
        name_tokens={rid: _norm_tokens(nm) for rid, nm in public_names.items()},
        n_teams=len(resolved),
    )
    try:
        ctx.current_week = int(storage.get_meta("current_week") or 0) or None
    except (TypeError, ValueError):
        ctx.current_week = None

    from leaguepage.site_build import _private_handles

    # With no public names this scanned handles and display names only, not
    # aliases -- where real first names live. The build audit is alias-aware
    # and runs AFTER the snapshot is frozen, so a private name in issue
    # prose passed QA, became immutable, and only then failed the build.
    ctx.private_handles = _private_handles(sorted(public_names.values()))

    # positional ranks as they stand today (freshness comparisons)
    try:
        from leaguepage.matchup_analysis import weekly_scores
        from leaguepage.team_analytics import positional_profile

        scores = weekly_scores(storage, league.league_id, 18)
        weeks_played = max((len(v) for v in scores.values()), default=0)
        profile = positional_profile(storage, league, weeks_played=weeks_played)
        ctx.positions_n = profile["n"]
        for pos in profile["positions"]:
            for rid, rank in profile["ranks"][pos].items():
                ctx.positional_ranks.setdefault(rid, {})[pos] = rank
    except Exception:  # analytics are advisory here; never block the gate on them
        pass

    # rosters: who was drafted by a team vs who is on it now
    drafted: dict[int, set[str]] = {}
    drafts = storage.get_drafts_for_league(league.league_id)
    if drafts:
        for p in storage.get_draft_picks(drafts[0]["draft_id"]):
            rid = p.get("roster_id")
            meta = p.get("metadata") or {}
            nm = " ".join(x for x in (meta.get("first_name"), meta.get("last_name")) if x).strip()
            if rid and nm:
                drafted.setdefault(rid, set()).add(nm)
    for r in storage.get_rosters(league.league_id):
        rid = r["roster_id"]
        rostered = set()
        for pid in (r.get("players") or []):
            p = storage.get_player(pid) or {}
            nm = p.get("full_name")
            if nm:
                rostered.add(nm)
                if p.get("position"):
                    ctx.player_positions[nm] = p["position"].upper()
                ctx.players[nm] = {"rid": rid, "position": (p.get("position") or "").upper(),
                                   "status": (p.get("injury_status") or None)}
        ctx.rosters[rid] = {"drafted": drafted.get(rid, set()), "rostered": rostered}

    # cross-section coherence: the league's own format, the raw Sleeper
    # names beside the public ones, and the other league's issue so a
    # paragraph shared between the two papers can be noticed.
    try:
        from leaguepage import coherence as _coh
        from leaguepage.team_names import sleeper_team_names

        positions = (storage.get_league(league.league_id) or {}).get("roster_positions") or []
        ctx.lineup_format = ("superflex" if "SUPER_FLEX" in positions
                             else ("1qb" if "QB" in positions else None))
        ctx.sleeper_names = {rid: nm for rid, nm in sleeper_team_names(storage, league).items() if nm}
        ctx.callsigns = _coh.callsigns_from_names(public_names)
        ctx.other_league_paragraphs, ctx.other_league_label = _other_league_text(
            league, season, issue_key)
    except Exception:  # advisory; never block the gate on it
        pass

    # this week's actual pairings, for "matchup changed" freshness
    if week:
        try:
            from leaguepage.matchup_analysis import analyze_week

            analysis = analyze_week(storage, league, week)
            for m in (analysis or {}).get("matchups", []):
                ctx.current_pairings.add(
                    frozenset(t["roster_id"] for t in m["teams"]))
        except Exception:
            pass

    from leaguepage.draft_analysis import slugify

    for nm in public_names.values():
        ctx.team_slugs.add(slugify(nm))
    return ctx


def _other_league_text(league: League, season: str, issue_key: str) -> tuple[set[str], str | None]:
    """Normalised paragraphs of the other league's same issue: what is on
    the Desk for it now, and whatever it has already published."""
    import glob
    import json

    from leaguepage import coherence as _coh
    from leaguepage import config as _cfg
    from leaguepage.issue_builder import issue_dir

    other = next((lg for lg in _cfg.LEAGUES if lg.slug != league.slug), None)
    if other is None:
        return set(), None
    texts: list[str] = []
    idir = issue_dir(other, season, issue_key)
    for path in ([idir / "lowdown" / "lowdown.md"] + sorted(idir.glob("sections/*.md"))
                 + sorted(idir.glob("matchups/*/draft.md"))):
        if path.exists():
            texts.append(path.read_text(encoding="utf-8"))
    for path in sorted(glob.glob(str(_cfg.PUBLISHED_DIR / other.slug / season / f"{issue_key}*.json"))):
        try:
            snap = json.loads(_read_text(path))
        except Exception:
            continue
        texts += [sec.get("content_md") or "" for sec in snap.get("sections", [])]
    return _coh.other_league_paragraphs(texts), f"{other.display_name} {issue_key}"


def _read_text(path: str) -> str:
    from pathlib import Path

    return Path(path).read_text(encoding="utf-8")


# ------------------------------------------------------------- identity


_ROSTER_N_RE = re.compile(r"\broster\s+\d+\b", re.I)
_ROSTER_SLUG_RE = re.compile(r"\broster-\d+\b")
# An internal matchup slug looks like "roster-6-vs-all-barkley-no-bite": at
# least one side is multi-segment. "man-vs-machine" and "us-vs-them" are
# ordinary English and were being blocked as leaked internals, with no
# override path. A gate that blocks a good sentence gets switched off.
_MATCHUP_SLUG_RE = re.compile(
    r"\b(?=[a-z0-9-]*-vs-)"
    r"(?:[a-z0-9]+-){2,}vs-[a-z0-9]+(?:-[a-z0-9]+)*"
    r"|[a-z0-9]+-vs(?:-[a-z0-9]+){2,}\b")
_PENDING_NAME_RE = re.compile(r"team[\s-]*name[\s-]*(pending|tbd|unknown)", re.I)

# A heading in a per-team section: "### 4. Jesse (-137)"
_HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*$", re.M)
_HEAD_NUM_RE = re.compile(r"^\s*\d+[.)]\s*")
_HEAD_SCORE_RE = re.compile(r"\s*\(([+-]?\d[\d,.]*)\)\s*$")


def _headings(text: str) -> list[tuple[int, str]]:
    return [(len(m.group(1)), m.group(2).strip()) for m in _HEADING_RE.finditer(text)]


def _team_headings(text: str, ctx: QAContext) -> list[str]:
    """The headings in this section that are supposed to name teams.

    Two filters, and both matter. First the LEVEL: a per-team section
    ("1. Los Bandidos (-42)", ten times) sits at one heading level while the
    section's own title sits at another. Then the SHAPE: a structural
    heading like "Second Opinions" can share the team level, so once most of
    the level is numbered, only the numbered ones are treated as team
    entries. Without the shape filter the check nags about every appendix."""
    if not ctx.n_teams:
        return []
    by_level: dict[int, list[str]] = {}
    for lvl, h in _headings(text):
        by_level.setdefault(lvl, []).append(h)
    need = max(3, round(0.6 * ctx.n_teams))
    candidates = [lvl for lvl, hs in by_level.items() if len(hs) >= need]
    if not candidates:
        return []
    heads = by_level[max(candidates)]
    numbered = [h for h in heads if _HEAD_NUM_RE.match(h)]
    return numbered if len(numbered) >= 0.6 * len(heads) else heads


def _resolve_team_heading(raw: str, ctx: QAContext) -> tuple[int | None, set[str]]:
    """(roster_id, foreign_tokens) for a heading that should name a team.

    A shortened form of the current name is fine — "The Dude" for "The Dude
    Abides (The Dude)". What is not fine is a token that appears in no
    current team name at all: that is a stale handle, an old nickname, or
    somebody else's team."""
    cand = _HEAD_SCORE_RE.sub("", _HEAD_NUM_RE.sub("", raw)).strip()
    toks = _norm_tokens(cand)
    if not toks:
        return None, set()
    best_rid, best_overlap = None, 0
    for rid, name_toks in ctx.name_tokens.items():
        overlap = len(toks & name_toks)
        if overlap > best_overlap:
            best_rid, best_overlap = rid, overlap
    if best_rid is None:
        return None, toks
    return best_rid, toks - ctx.name_tokens[best_rid]


def _check_identity(text: str, module_key: str, ctx: QAContext) -> list[Finding]:
    out: list[Finding] = []
    for m in _ROSTER_N_RE.finditer(text):
        out.append(Finding(
            IDENTITY, BLOCKER, "Unresolved roster placeholder",
            f"'{m.group(0)}' is an internal roster id, not a team name. Set a "
            "public name on the Team names panel, then re-check.",
            module_key, excerpt=_context_of(text, m.start(), m.end())))
    for rx, what in ((_ROSTER_SLUG_RE, "internal roster slug"),
                     (_MATCHUP_SLUG_RE, "internal matchup slug")):
        for m in rx.finditer(text):
            out.append(Finding(
                IDENTITY, BLOCKER, f"Internal identifier in prose ({what})",
                f"'{m.group(0)}' is a {what} used for file paths and URLs. It "
                "should never appear in reader-facing copy.",
                module_key, excerpt=_context_of(text, m.start(), m.end())))
    for m in _PENDING_NAME_RE.finditer(text):
        f = Finding(
            IDENTITY, BLOCKER, "'team name pending' in published prose",
            "The copy says a team has no name yet. Either the team now has "
            "one, or it still needs one — either way this line cannot ship.",
            module_key, excerpt=_context_of(text, m.start(), m.end()))
        named = sorted(ctx.public_names.values())
        if named:
            f.evidence = [f"{len(named)} of {ctx.n_teams} rosters now resolve to a "
                          "public name"]
        out.append(f)

    # per-team heading blocks: only run when the section really is one
    team_heads = _team_headings(text, ctx)
    if team_heads:
        for raw in team_heads:
            rid, foreign = _resolve_team_heading(raw, ctx)
            if rid is None:
                out.append(Finding(
                    IDENTITY, WARNING, "Heading names no known team",
                    f"'{raw}' sits in a per-team section but matches none of "
                    "this league's current public team names.",
                    module_key, excerpt=raw,
                    evidence=[f"current names: {', '.join(sorted(ctx.public_names.values()))}"]))
            elif foreign:
                current = ctx.public_names[rid]
                out.append(Finding(
                    IDENTITY, WARNING, "Heading disagrees with the public team name",
                    f"'{raw}' carries {', '.join(sorted(foreign))}, which is not "
                    f"part of the current public name.",
                    module_key, excerpt=raw, suggestion=_HEAD_NUM_RE.match(raw).group(0) + current
                    if _HEAD_NUM_RE.match(raw) else current,
                    evidence=[f"current public name: {current}"]))
    return out


def _check_privacy(text: str, module_key: str, ctx: QAContext) -> list[Finding]:
    # The build audit's matcher, so what passes here cannot fail there.
    # A case-sensitive test let "the commish" through QA into a frozen
    # revision that the (case-insensitive) build audit then refused.
    from leaguepage.privacy import handle_re

    out = []
    for handle in ctx.private_handles:
        if handle_re(handle).search(text):
            out.append(Finding(
                PRIVACY, BLOCKER, "Private Sleeper handle in public prose",
                "A login handle from the private manager file appears in copy "
                "intended for the public site. Use the public team name or a "
                "confirmed league nickname.",
                module_key, excerpt="(handle withheld from this report)",
                privacy=True))
    return out


# ---------------------------------------------------------- placeholders

# Markers, not words that happen to appear in a marker. "Playoffs are coming
# soon for this roster" is a sentence; "Coming soon" alone on a line is a
# stub. And "weeks XXX through XXI" is a Roman numeral, which is exactly how
# this newsletter numbers its volumes.
PLACEHOLDER_PATTERNS: list[tuple[str, str]] = [
    (r"Preview pending", "an unwritten matchup preview"),
    (r"(?m)^[\s>*_-]*Coming soon\b[\s.!]*$", "a 'coming soon' stub"),
    (r"\bTBD\b", "a TBD marker"),
    (r"\bTODO\b", "a TODO marker"),
    (r"\bFIXME\b", "a FIXME marker"),
    # This newsletter numbers its volumes in Roman numerals, so a bare XXX
    # in prose is far more likely to be thirty than a placeholder. It counts
    # only where a marker actually sits: alone on a line, or bracketed.
    (r"(?m)^[\s>*_-]*X{3,}[\s.!]*$|\[\s*X{3,}\s*\]", "an XXX marker"),
    (r"\bLorem ipsum\b", "placeholder latin"),
    (r"<\s*placeholder\s*>", "a placeholder tag"),
    (r"\[\s*(?:insert|fill in|add)[^\]]*\]", "an authoring instruction"),
]


def _check_placeholders(text: str, module_key: str, ctx: QAContext) -> list[Finding]:
    out = []
    for pat, what in PLACEHOLDER_PATTERNS:
        for m in re.finditer(pat, text, re.I):
            out.append(Finding(
                PLACEHOLDER, BLOCKER, "Raw placeholder in prose",
                f"This is {what}. Replace it or cut the section.",
                module_key, excerpt=_context_of(text, m.start(), m.end())))
    for marker in BLOCKED_MARKERS:
        if marker in text:
            out.append(Finding(
                PLACEHOLDER, BLOCKER, "Blocked draft marker",
                f"'{marker}' marks material that must never publish.",
                module_key, excerpt=marker))
    return out


# ------------------------------------------------------------ formatting

# An unrendered heading has its '#' and its text on the SAME line. Using \s
# here instead of [ \t] made this match across a newline, so a table whose
# first column header is "#" — the standings and ranking tables — was
# reported as broken markup. The gate false-positiving on ordinary tables is
# worse than the leak it was looking for.
_LEAK_HEADING_RE = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]+\S", re.M)
_LEAK_BOLD_RE = re.compile(r"\*\*\S|\S\*\*")
_LEAK_LINK_RE = re.compile(r"\[[^\]\n]{1,80}\]\([^)\n]{1,200}\)")
_EMPTY_HEADING_RE = re.compile(r"<h([1-6])[^>]*>\s*</h\1>", re.I)
_DANGLING_BULLET_RE = re.compile(r"^\s*[-*+]\s*$", re.M)
_LONG_HEADING = 110


def scrub_code(text: str) -> str:
    """Blank out code spans and fences, keeping offsets intact.

    `a**b` in a code span is not a bold marker that failed to render, and
    blocking on it made a config example unpublishable."""
    out = re.sub(r"```.*?```", lambda m: " " * len(m.group(0)), text, flags=re.S)
    return re.sub(r"`[^`\n]*`", lambda m: " " * len(m.group(0)), out)


def _rendered_text(content_md: str) -> str:
    html = prose.render(strip_editorial_comments(content_md))
    # An inline code span is deliberate, so `a**b` inside one is not a bold
    # marker that failed to render. A <pre> block is deliberately NOT
    # stripped: a heading indented four spaces becomes one, and that is
    # exactly the defect the unrendered-heading check exists to catch.
    html = re.sub(r"(?is)(?<!<pre>)<code\b[^>]*>(.*?)</code>", " ", html)
    text = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", html)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|li|tr|h[1-6]|td|th)>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    return text


def _check_formatting(content_md: str, module_key: str, ctx: QAContext) -> list[Finding]:
    out: list[Finding] = []
    html = prose.render(strip_editorial_comments(content_md))
    rendered = _rendered_text(content_md)

    for m in _LEAK_HEADING_RE.finditer(rendered):
        out.append(Finding(
            FORMATTING, BLOCKER, "Markdown heading did not render",
            "A '#' heading is showing up as literal text in the body. It is "
            "usually indented four spaces, or glued to the line above.",
            module_key, excerpt=_context_of(rendered, m.start(), m.end())))
    if _LEAK_BOLD_RE.search(rendered):
        m = _LEAK_BOLD_RE.search(rendered)
        out.append(Finding(
            FORMATTING, BLOCKER, "Bold markers rendered as text",
            "'**' is showing in the rendered page instead of producing bold.",
            module_key, excerpt=_context_of(rendered, m.start(), m.end())))
    if _LEAK_LINK_RE.search(rendered):
        m = _LEAK_LINK_RE.search(rendered)
        out.append(Finding(
            FORMATTING, BLOCKER, "Link syntax rendered as text",
            "Markdown link syntax survived rendering, so readers see the "
            "brackets instead of a link.",
            module_key, excerpt=_context_of(rendered, m.start(), m.end())))
    for m in re.finditer(r'src="([^"]+)"', html):
        src = m.group(1)
        if src.startswith(("http://", "https://", "data:")):
            continue
        out.append(Finding(
            FORMATTING, BLOCKER, "Image source is not publishable",
            f"'{src}' is a relative path. The published page is served from "
            "a different directory, so the image will not load.",
            module_key, excerpt=src))
    for m in _EMPTY_HEADING_RE.finditer(html):
        out.append(Finding(
            FORMATTING, BLOCKER, "Empty heading",
            "A heading renders with no text in it.", module_key,
            excerpt=m.group(0)))
    for m in re.finditer(r'href="([^"]+)"', html):
        href = m.group(1)
        if href.startswith(("http://", "https://", "#", "mailto:")):
            continue
        if href.startswith(("fn:", "fnref:")):   # markdown footnotes
            continue
        out.append(Finding(
            FORMATTING, BLOCKER, "Link target is not publishable",
            f"'{href}' is a relative or local path. Published issues render "
            "from a frozen snapshot, so only absolute URLs and in-page "
            "anchors survive.", module_key, excerpt=href))
    for m in _DANGLING_BULLET_RE.finditer(content_md):
        out.append(Finding(
            FORMATTING, WARNING, "Empty bullet",
            "A list item has no content.", module_key,
            excerpt=_context_of(content_md, m.start(), m.end())))

    heads = _headings(content_md)
    seen: dict[str, int] = {}
    for _, h in heads:
        key = h.strip().lower()
        seen[key] = seen.get(key, 0) + 1
        if seen[key] == 2:
            out.append(Finding(
                FORMATTING, WARNING, "Duplicate heading",
                f"'{h}' appears more than once in this section.",
                module_key, excerpt=h))
    for level, h in heads:
        if len(h) > _LONG_HEADING:
            out.append(Finding(
                FORMATTING, WARNING, "Heading may have swallowed a paragraph",
                f"This heading is {len(h)} characters long, which usually means "
                "a missing line break after it.",
                module_key, excerpt=h[:160]))
    return out


# ------------------------------------------------------------------ copy
#
# Mechanical only. Everything here has an unambiguous correct form; nothing
# here has a view about voice.

# a doubled stop that is not an ellipsis
_DOUBLE_STOP_RE = re.compile(r"(?<=\w)(\.\.(?!\.)|,,|;;|::)")
_REPEAT_WORD_RE = re.compile(r"\b(\w{3,})(\s+)\1\b", re.I)
_REPEAT_OK = {"had", "that", "is", "no", "very", "ha", "blah", "yeah", "so"}
# a full stop wearing a comma: lowercase word, comma, then a capitalized
# pronoun or determiner that can only start a new sentence.
_SPLICE_FOLLOWERS = ("The", "This", "That", "These", "Those", "It", "We", "He",
                     "She", "They", "There", "Our", "My", "His", "Her", "Their",
                     "Your", "You", "Then", "Now")
_SPLICE_RE = re.compile(
    r"(?<=[a-z]),\s+(" + "|".join(_SPLICE_FOLLOWERS) + r")\s+([a-z]\w*)")
_SPACE_BEFORE_PUNCT_RE = re.compile(r"\s+([,.;:!?])(?=\s|$)")
_MISSING_SPACE_RE = re.compile(r"(?<=[a-z]{2}),(?=[A-Za-z])")
_TOO_RE = re.compile(r"\b(way|far|much|so|all)\s+to\s+"
                     r"(many|much|few|little|late|early|big|small|long|short|often)\b", re.I)
_LOOSE_APOSTROPHE_RE = re.compile(r"\b(\w+)\s+'(s|t|re|ve|ll|d)\b")


def _context_of(text: str, start: int, end: int, width: int = 55) -> str:
    a = max(0, start - width)
    b = min(len(text), end + width)
    snippet = text[a:b].replace("\n", " ").strip()
    return ("…" if a else "") + snippet + ("…" if b < len(text) else "")


def _copy_finding(text: str, m, module_key: str, title: str, detail: str,
                  repl: str, *, upto: int | None = None) -> Finding:
    end = upto if upto is not None else m.end()
    before, after = _fix_window(text, m.start(), end, repl)
    return Finding(COPY, WARNING, title, detail, module_key,
                   excerpt=_context_of(text, m.start(), end),
                   suggestion=after, fix_from=before, fix_to=after)


def _check_copy(text: str, module_key: str, ctx: QAContext) -> list[Finding]:
    out: list[Finding] = []
    # never copy-edit inside a code span or fence
    scrubbed = re.sub(r"```.*?```", lambda m: " " * len(m.group(0)), text, flags=re.S)
    scrubbed = re.sub(r"`[^`\n]*`", lambda m: " " * len(m.group(0)), scrubbed)

    for m in _DOUBLE_STOP_RE.finditer(scrubbed):
        got = m.group(1)
        out.append(_copy_finding(
            scrubbed, m, module_key, "Doubled punctuation",
            f"'{got}' is almost certainly a typing slip.", got[0]))
    for m in _REPEAT_WORD_RE.finditer(scrubbed):
        if m.group(1).lower() in _REPEAT_OK:
            continue
        out.append(_copy_finding(
            scrubbed, m, module_key, "Word repeated",
            f"'{m.group(1)}' appears twice in a row.", m.group(1)))
    for m in _SPLICE_RE.finditer(scrubbed):
        # "…, The Dude printed it" is a team, not a new sentence
        if _looks_like_a_name(scrubbed[m.start(1):m.start(1) + 60], ctx):
            continue
        out.append(_copy_finding(
            scrubbed, m, module_key, "Two sentences joined by a comma",
            f"'{m.group(1)}' starts what reads as a new sentence, but the "
            "clause before it ends in a comma.", ". ", upto=m.start(1)))
    for m in _SPACE_BEFORE_PUNCT_RE.finditer(scrubbed):
        out.append(_copy_finding(
            scrubbed, m, module_key, "Space before punctuation",
            "There is whitespace between the last word and its punctuation.",
            m.group(1)))
    for m in _MISSING_SPACE_RE.finditer(scrubbed):
        out.append(_copy_finding(
            scrubbed, m, module_key, "Missing space after a comma",
            "Two words are run together across a comma.", ", "))
    for m in _TOO_RE.finditer(scrubbed):
        out.append(_copy_finding(
            scrubbed, m, module_key, "'to' where 'too' belongs",
            f"'{m.group(0)}' wants the adverb, not the preposition.",
            m.group(0).replace(" to ", " too ", 1)))
    for m in _LOOSE_APOSTROPHE_RE.finditer(scrubbed):
        out.append(_copy_finding(
            scrubbed, m, module_key, "Detached apostrophe",
            "A contraction or possessive has drifted away from its word.",
            f"{m.group(1)}'{m.group(2)}"))
    for para in re.split(r"\n\s*\n", scrubbed):
        if para.count('"') % 2 == 1:
            out.append(Finding(
                COPY, WARNING, "Unclosed quotation mark",
                "This paragraph has an odd number of straight double quotes.",
                module_key, excerpt=para.strip()[:160]))
    return out


def _looks_like_a_name(tail: str, ctx: QAContext) -> bool:
    toks = _norm_tokens(tail[:40])
    return any(toks & nt for nt in ctx.name_tokens.values())


_SENT_END_RE = re.compile(r"[.!?][\"')\]]*\s")


def _fix_window(text: str, start: int, end: int, repl: str,
                *, pad: int = 70) -> tuple[str, str]:
    """(exact original window, corrected window) around one mechanical fix.

    Scoped to a readable span rather than a whole markdown paragraph, and
    snapped to sentence boundaries when they are near, so Accept performs a
    literal replacement the Commissioner can read in full before agreeing to
    it."""
    a, b = max(0, start - pad), min(len(text), end + pad)
    last = None
    for last in _SENT_END_RE.finditer(text[a:start]):
        pass
    if last:
        a += last.end()
    elif a:
        while a > 0 and text[a - 1] not in " \n\t":
            a -= 1
    nxt = _SENT_END_RE.search(text[end:b])
    if nxt:
        b = end + nxt.end()
    elif b < len(text):
        while b < len(text) and text[b] not in " \n\t":
            b += 1
    return text[a:b].strip(), (text[a:start] + repl + text[end:b]).strip()


# ------------------------------------------------------------- freshness

_POS_RANK_RE = re.compile(
    r"\b(QB|RB|WR|TE|K|DEF|DST)\b[^.\n]{0,24}?\branks?\s+#?(\d+)\s*(?:/|of)\s*(\d+)", re.I)
_TIME_RELATIVE_RE = re.compile(
    r"\b(next week|this week|next Thursday|later today|tonight|tomorrow)\b", re.I)


def _check_freshness(content_md: str, module_key: str, ctx: QAContext,
                     *, published: bool) -> list[Finding]:
    out: list[Finding] = []

    # rank claims against the ranks as they stand right now
    for _, section_text, rid in _team_blocks(content_md, ctx):
        for m in _POS_RANK_RE.finditer(section_text):
            pos = m.group(1).upper()
            pos = "DEF" if pos == "DST" else pos
            claimed = int(m.group(2))
            now = (ctx.positional_ranks.get(rid) or {}).get(pos)
            if now is not None and now != claimed:
                out.append(Finding(
                    FRESHNESS, WARNING, "Rank claim no longer matches the data",
                    f"The copy says {pos} ranks {claimed}; today it ranks {now}.",
                    module_key, excerpt=_context_of(section_text, m.start(), m.end()),
                    evidence=[f"when written: {pos} #{claimed}",
                              f"now: {pos} #{now} of {ctx.positions_n}",
                              f"team: {ctx.public_names.get(rid, rid)}"]))
        # players written about who have since left the roster
        roster = ctx.rosters.get(rid) or {}
        gone = (roster.get("drafted", set()) - roster.get("rostered", set()))
        for name in sorted(gone):
            if re.search(rf"\b{re.escape(name)}\b", section_text):
                out.append(Finding(
                    FRESHNESS, WARNING, "Player has left the roster since this was written",
                    f"{name} is discussed here but is no longer on "
                    f"{ctx.public_names.get(rid, 'this team')}'s roster.",
                    module_key, excerpt=name,
                    evidence=[f"drafted by {ctx.public_names.get(rid, rid)}",
                              "not on the current synced roster"]))

    # time-relative phrasing in an issue the calendar has moved past
    if published and ctx.current_week and ctx.week and ctx.current_week > ctx.week:
        for m in _TIME_RELATIVE_RE.finditer(content_md):
            out.append(Finding(
                FRESHNESS, WARNING, "Time-relative phrasing has gone stale",
                f"'{m.group(0)}' was written for week {ctx.week}; the league is "
                f"now on week {ctx.current_week}.",
                module_key, excerpt=_context_of(content_md, m.start(), m.end())))
    return out


def _team_blocks(content_md: str, ctx: QAContext) -> list[tuple[str, str, int]]:
    """(heading, body, roster_id) for each heading that resolves to a team."""
    heads = list(_HEADING_RE.finditer(content_md))
    blocks = []
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(content_md)
        raw = m.group(2).strip()
        rid, _foreign = _resolve_team_heading(raw, ctx)
        if rid is not None:
            blocks.append((raw, content_md[m.end():end], rid))
    return blocks


# -------------------------------------------------- analytical consistency

# The calibration decision this guards (docs/HANDOFF.md, 2026-08-30): overall
# FantasyPros ECR ranks every kicker and defense below the draftable range,
# so a K/DST "reach" measured against the overall board is an artifact of the
# reference board's shape, not a roster decision. Ranking teams by summed raw
# deltas without saying so hands the reader a conclusion the method cannot
# support.
_ST_DISCLOSURE_RE = re.compile(
    r"below the draftable range|special[- ]teams? (artifact|outlier|premium is)|"
    r"within[- ]position|reference[- ]board artifact|overall ECR ranks (kickers|special)",
    re.I)
_SIGNED_NUM_RE = re.compile(r"\bminus[- ]?\d+|\(-\d+\)|[-−]\d{2,}\b")
_ST_WORD_RE = re.compile(r"\b(kicker|kickers|defense|defence|defenses|DST|D/ST)\b", re.I)


def _check_analytics(content_md: str, module_key: str, ctx: QAContext) -> list[Finding]:
    heads = _team_headings(content_md, ctx)
    if sum(1 for h in heads if _HEAD_SCORE_RE.search(h) or _HEAD_NUM_RE.match(h)) < 3:
        return []
    st_hits = set()
    for line in content_md.splitlines():
        if not _SIGNED_NUM_RE.search(line):
            continue
        if _ST_WORD_RE.search(line):
            st_hits.add(line.strip()[:90])
            continue
        for name, pos in ctx.player_positions.items():
            if pos in ("K", "DEF", "DST") and re.search(rf"\b{re.escape(name)}\b", line):
                st_hits.add(line.strip()[:90])
                break
    if len(st_hits) < 2 or _ST_DISCLOSURE_RE.search(content_md):
        return []
    return [Finding(
        ANALYTICS, WARNING, "Team ranking leans on raw K/DST consensus deltas",
        "This section ranks teams by summed deviation from the consensus board "
        "and attributes a meaningful share of the result to kicker and defense "
        "picks. The calibrated Draft page treats those deltas as artifacts of "
        "the reference board — overall ECR ranks every K and DST below the "
        "draftable range while lineups force every team to draft them — and "
        "keeps them out of the headline Reaches and Steals. Either qualify the "
        "method here or say the ranking is a special-teams tax table.",
        module_key,
        excerpt=" / ".join(sorted(st_hits)[:4]),
        evidence=[f"{len(st_hits)} special-teams value lines carry the ranking",
                  "docs/HANDOFF.md — calibration tranche, 2026-08-30",
                  "draft_value.headline_deviations excludes K/DST by design"])]


# ------------------------------------------------------------------ report


def check_sections(sections: list[dict], ctx: QAContext, *,
                   published: bool = False) -> list[Finding]:
    """Run every check over assembled sections.

    Each section is {module_key, title, content_md}. A section with no
    content is a blocker in itself: an included module that says nothing is
    a dead link in the table of contents."""
    findings: list[Finding] = []
    for s in sections:
        key = s.get("module_key")
        text = (s.get("content_md") or "").strip()
        if not text:
            if s.get("kind") == "auto":
                continue
            findings.append(Finding(
                PLACEHOLDER, BLOCKER, "Included section is empty",
                f"'{s.get('title') or key}' is in the issue but has no copy. "
                "Write it or drop it from the issue.", key))
            continue
        findings += _check_privacy(text, key, ctx)
        findings += _check_identity(text, key, ctx)
        findings += _check_placeholders(text, key, ctx)
        findings += _check_formatting(text, key, ctx)
        findings += _check_copy(text, key, ctx)
        findings += _check_freshness(text, key, ctx, published=published)
        findings += _check_analytics(text, key, ctx)
    findings += _check_coherence(sections, ctx)
    return _dedupe(findings)


def _check_coherence(sections: list[dict], ctx: QAContext) -> list[Finding]:
    """The between-sections checks, run once over the whole issue."""
    from leaguepage import coherence as _coh

    live = [{"module_key": s.get("module_key"), "title": s.get("title"),
             "content_md": (s.get("content_md") or "")}
            for s in sections if (s.get("content_md") or "").strip()]
    if not live:
        return []
    claims = []
    for s in live:
        for _, body, rid in _team_blocks(s["content_md"], ctx):
            for m in _POS_RANK_RE.finditer(body):
                pos = m.group(1).upper()
                claims.append({"module_key": s["module_key"], "rid": rid,
                               "pos": "DEF" if pos == "DST" else pos,
                               "claimed": int(m.group(2))})
    cctx = {"format": ctx.lineup_format, "public_names": ctx.public_names,
            "sleeper_names": ctx.sleeper_names, "callsigns": ctx.callsigns,
            "players": ctx.players, "former_names": ctx.former_names,
            "other_league_paragraphs": ctx.other_league_paragraphs,
            "other_league_label": ctx.other_league_label}
    out = []
    for f in _coh.check(live, cctx, rank_claims=claims):
        out.append(Finding(f["category"], WARNING, f["title"], f["detail"],
                           f.get("module_key"), excerpt=f.get("excerpt"),
                           suggestion=f.get("suggestion"),
                           evidence=list(f.get("evidence") or [])))
    return out


def _dedupe(findings: list[Finding]) -> list[Finding]:
    seen, out = set(), []
    for f in findings:
        if f.finding_id in seen:
            continue
        seen.add(f.finding_id)
        out.append(f)
    sev = {BLOCKER: 0, WARNING: 1}
    out.sort(key=lambda f: (sev[f.severity], CATEGORY_ORDER.index(f.category)
                            if f.category in CATEGORY_ORDER else 99,
                            f.module_key or ""))
    return out


def report(findings: list[Finding], *, ignored: set[str] | None = None) -> dict:
    """The Publication Check panel's whole model."""
    ignored = ignored or set()
    live = [f for f in findings if not (f.severity == WARNING
                                        and f.finding_id in ignored)]
    blockers = [f for f in live if f.severity == BLOCKER]
    warnings = [f for f in live if f.severity == WARNING]
    groups: list[dict] = []
    for cat in CATEGORY_ORDER:
        items = [f for f in live if f.category == cat]
        if items:
            groups.append({"category": cat, "label": CATEGORY_LABELS[cat],
                           "findings": [f.as_dict() for f in items]})
    return {
        "ready": not blockers,
        "blockers": [f.as_dict() for f in blockers],
        "warnings": [f.as_dict() for f in warnings],
        "groups": groups,
        "ignored_count": len(findings) - len(live),
        "headline": (f"READY · 0 blockers · {len(warnings)} warning"
                     f"{'' if len(warnings) == 1 else 's'}" if not blockers
                     else f"NOT READY · {len(blockers)} blocker"
                          f"{'' if len(blockers) == 1 else 's'}"
                          f" · {len(warnings)} warning"
                          f"{'' if len(warnings) == 1 else 's'}"),
        "has_privacy_blocker": any(f.privacy for f in blockers),
    }


# ------------------------------------------------------------ entry points


def check_issue(storage: Storage, league: League, season: str, issue_key: str,
                *, base_dir=None, week: int | None = None) -> dict:
    """QA the issue as it stands in the editorial workspace right now."""
    from leaguepage.issue_builder import assemble_issue

    ctx = build_context(storage, league, season, issue_key, week=week)
    assembled = assemble_issue(storage, league, season, issue_key,
                               base_dir=base_dir, week=week)
    sections = [s for s in assembled["sections"] if s.get("included", True)]
    findings = check_sections(sections, ctx)
    rep = report(findings, ignored=ignored_findings(storage, league.slug, season, issue_key))
    rep["issue_key"] = issue_key
    return rep


def check_snapshot(storage: Storage, league: League, snapshot: dict) -> dict:
    """QA an already-published, frozen snapshot. Same checks, plus the
    freshness ones that only make sense once time has passed."""
    season = snapshot["season"]
    issue_key = snapshot["issue_key"]
    week = None
    if issue_key.startswith("week-"):
        try:
            week = int(issue_key.removeprefix("week-"))
        except ValueError:
            week = None
    ctx = build_context(storage, league, season, issue_key, week=week)
    findings = check_sections(snapshot.get("sections", []), ctx, published=True)
    rep = report(findings, ignored=ignored_findings(storage, league.slug, season, issue_key))
    rep["issue_key"] = issue_key
    rep["published_at"] = snapshot.get("published_at")
    return rep


# ------------------------------------------------------------ ignore store

def _ignore_key(league_slug: str, season: str, issue_key: str) -> str:
    return f"qa_ignored:{league_slug}:{season}:{issue_key}"


def ignored_findings(storage: Storage, league_slug: str, season: str,
                     issue_key: str) -> set[str]:
    import json

    raw = storage.get_meta(_ignore_key(league_slug, season, issue_key))
    try:
        return set(json.loads(raw)) if raw else set()
    except (ValueError, TypeError):
        return set()


def ignore_finding(storage: Storage, league_slug: str, season: str,
                   issue_key: str, finding_id: str) -> None:
    """Dismiss one warning. Blockers are unaffected: report() only honors
    ignores for warnings, so a dismissed id can never unblock a publish."""
    import json

    current = ignored_findings(storage, league_slug, season, issue_key)
    current.add(finding_id)
    storage.set_meta(_ignore_key(league_slug, season, issue_key),
                     json.dumps(sorted(current)))


def unignore_finding(storage: Storage, league_slug: str, season: str,
                     issue_key: str, finding_id: str) -> None:
    import json

    current = ignored_findings(storage, league_slug, season, issue_key)
    current.discard(finding_id)
    storage.set_meta(_ignore_key(league_slug, season, issue_key),
                     json.dumps(sorted(current)))


# ------------------------------------------------------------- receipts
#
# A receipt is a quotation of the Commissioner attached to a verdict, which
# makes it the highest-consequence thing this site publishes. The gate
# checks the things that would make one dishonest rather than merely ugly.

RECEIPT_REQUIRED = ("quote", "href", "issue_key")


def check_receipts(receipts: list[dict], ctx: QAContext) -> list[Finding]:
    """Validate publishable receipts before they can reach a reader.

    The paraphrase rule is the important one: a take the Commissioner edited
    when he tracked it is not a quotation, and presenting it as one puts
    words in his mouth in his own archive."""
    out: list[Finding] = []
    for r in receipts:
        label = (r.get("quote") or "")[:60]
        for field in RECEIPT_REQUIRED:
            if not r.get(field):
                out.append(Finding(
                    IDENTITY, BLOCKER, "Receipt is missing provenance",
                    f"A public receipt needs its {field}: a quotation without "
                    "the issue it came from cannot be checked by a reader.",
                    "receipts", excerpt=label))
        if r.get("verbatim") is False and r.get("presented_as_quote"):
            out.append(Finding(
                IDENTITY, BLOCKER, "Paraphrase presented as a quotation",
                "This take was edited when it was tracked, so it is not the "
                "published wording. Public surfaces must mark it as a "
                "paraphrase rather than wrap it in quotation marks.",
                "receipts", excerpt=label))
        for handle in ctx.private_handles:
            if re.search(rf"\b{re.escape(handle)}\b", r.get("quote") or ""):
                out.append(Finding(
                    PRIVACY, BLOCKER, "Private handle inside a receipt",
                    "The quoted text carries a login handle from the private "
                    "manager file.", "receipts",
                    excerpt="(handle withheld from this report)", privacy=True))
        for field in ("note", "confidence", "recommended_status"):
            if r.get(field):
                out.append(Finding(
                    PRIVACY, BLOCKER, f"Commissioner-only field on a public receipt",
                    f"'{field}' is private editorial state and must not travel "
                    "with a receipt to the public build.", "receipts",
                    excerpt=label, privacy=True))
        quote = r.get("quote") or ""
        if _ROSTER_N_RE.search(quote) or _PENDING_NAME_RE.search(quote):
            out.append(Finding(
                IDENTITY, BLOCKER, "Unresolved identity inside a receipt",
                "The quoted text still carries a roster placeholder.",
                "receipts", excerpt=label))
        subject = r.get("subject_name")
        if subject and subject not in ctx.public_names.values():
            out.append(Finding(
                FRESHNESS, WARNING, "Receipt names a stale team",
                f"'{subject}' is not a current public team name.",
                "receipts", excerpt=label,
                evidence=[f"current names: {', '.join(sorted(ctx.public_names.values()))}"]))
    return _dedupe(out)
