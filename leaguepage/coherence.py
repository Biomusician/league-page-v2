"""Does the issue read like one newspaper, or like six generators describing
the same league?

Every section is drafted on its own, from its own brief, and each one is
usually fine alone. What goes wrong happens between them: a fade written
for a 1QB format in a Superflex league; a team named by the Sleeper string
in one section and its public name in another; a player praised as a
strength in a preview while another section reports him out; the same
player written up in four places; a paragraph lifted wholesale from the
other league's issue. None of that needs a language model to find. It
needs the roster, the league settings and a word boundary.

These are warnings. A callback is intentional, a fade can mention a
format on purpose, and the Commissioner reads the finding and decides.
Nothing here edits a sentence.

Pure functions over plain data: `check()` takes the assembled sections and
a context dict and returns finding dicts; `pubqa` turns them into its own
Finding type. Kept out of pubqa so that module's import graph stays flat.
"""
from __future__ import annotations

import re
from collections import defaultdict

OUT_STATUSES = {"IR", "Out", "Doubtful", "PUP", "Sus", "NA"}

# How many separate sections a player has to appear in before it is worth
# asking whether the paper is repeating itself. Three is a story told in the
# Lowdown, previewed in the matchups and written up again somewhere else.
SATURATION_SECTIONS = 3
# A paragraph short enough to recur by coincidence is not a duplicate.
DUPLICATE_MIN_WORDS = 25

# The digit form only. "One QB, two RB" is a lineup being described, not
# format advice; "1QB formats" and "in a 1QB league" are the imported kind.
_ONE_QB_RE = re.compile(r"\b1[- ]?QB\b", re.I)
_SUPERFLEX_RE = re.compile(r"\bsuper[- ]?flex\b", re.I)
_INJURY_WORDS_RE = re.compile(
    r"\b(injur|hurt|out\b|IR\b|unavailable|sidelined|questionable|doubtful|"
    r"suspend|reserve|ankle|hamstring|knee|birth defect)", re.I)
_TAG_RE = re.compile(r"\(([^()]{2,40})\)")
_WS_RE = re.compile(r"\s+")
_MARKUP_RE = re.compile(r"<[^>]+>|[*_#>`]+")


def _finding(category, title, detail, module_key, *, excerpt=None, evidence=None,
             severity="warning", suggestion=None) -> dict:
    return {"category": category, "severity": severity, "title": title,
            "detail": detail, "module_key": module_key, "excerpt": excerpt,
            "evidence": evidence or [], "suggestion": suggestion}


def _snippet(text: str, start: int, end: int, width: int = 55) -> str:
    a, b = max(0, start - width), min(len(text), end + width)
    return ("…" if a else "") + text[a:b].replace("\n", " ") + ("…" if b < len(text) else "")


def _paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def normalize_paragraph(text: str) -> str:
    """Lowercased words only, so markup and punctuation cannot hide a copy."""
    t = _MARKUP_RE.sub(" ", text or "").lower()
    t = re.sub(r"[^a-z0-9' ]+", " ", t)
    return _WS_RE.sub(" ", t).strip()


def _word_re(name: str) -> re.Pattern:
    return re.compile(rf"(?<![\w'])" + re.escape(name) + r"(?![\w'])")


# ------------------------------------------------------------- format

def format_mismatch(sections: list[dict], cctx: dict) -> list[dict]:
    """Copy written for the wrong lineup format.

    A fade that says "sit in 1QB formats" in a Superflex league, or a track
    quoting the Superflex consensus in a 1QB league, is generic advice that
    was never rewritten for this league's settings.
    """
    fmt = cctx.get("format")
    if fmt not in ("superflex", "1qb"):
        return []
    out = []
    for s in sections:
        text = s.get("content_md") or ""
        if fmt == "superflex":
            rx, wrong, right = _ONE_QB_RE, "1QB", "Superflex"
        else:
            rx, wrong, right = _SUPERFLEX_RE, "Superflex", "1QB"
        for m in rx.finditer(text):
            out.append(_finding(
                "coherence", "Copy written for the other lineup format",
                f"This league is {right}; the copy talks about {wrong} play. Advice "
                f"imported from a generic column, or a sentence that needs the "
                f"league's own format.",
                s["module_key"], excerpt=_snippet(text, m.start(), m.end()),
                evidence=[f"league roster positions say: {right}",
                          f"matched: '{m.group(0)}'"]))
    return out


# ------------------------------------------------------------- identity

def _name_tokens(name: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (name or "").lower()) if len(w) > 1}


def stale_team_names(sections: list[dict], cctx: dict) -> list[dict]:
    """A team called by a name the paper does not use.

    Two sources, both structural. The raw Sleeper name when it differs from
    the confirmed public name (`Stafford&Son` against `Stafford and Sons`),
    and any former name recorded for the roster. Either one in the prose
    means a section was written from a different name table than the rest.
    """
    public = cctx.get("public_names") or {}
    sleeper = cctx.get("sleeper_names") or {}
    former = dict(cctx.get("former_names") or {})
    candidates: list[tuple[str, int, str]] = []
    for rid, raw in sleeper.items():
        pub = public.get(rid)
        if not raw or not pub:
            continue
        raw_t, pub_t = _name_tokens(raw), _name_tokens(re.sub(r"\([^)]*\)", "", pub))
        if raw_t and raw_t != pub_t and not raw_t <= pub_t:
            candidates.append((raw, rid, "the Sleeper team name"))
    for name, rid in former.items():
        if name and public.get(rid) and _name_tokens(name) != _name_tokens(public[rid]):
            candidates.append((name, rid, "a former name"))
    out = []
    for s in sections:
        text = s.get("content_md") or ""
        for name, rid, kind in candidates:
            for m in _word_re(name).finditer(text):
                out.append(_finding(
                    "identity", "Team named by a name the paper does not use",
                    f"'{name}' is {kind} for {public.get(rid)}; the rest of the "
                    f"issue calls the team by its public name.",
                    s["module_key"], excerpt=_snippet(text, m.start(), m.end()),
                    evidence=[f"public name: {public.get(rid)}", f"roster {rid}"],
                    suggestion=public.get(rid)))
                break
    return out


def owner_attribution(sections: list[dict], cctx: dict) -> list[dict]:
    """"Kyle Pitts, TE (POP)" when Pitts is on somebody else's roster.

    The attribution convention in Tracks and Fades puts the owner's
    callsign or team name right after the player. That is structured enough
    to check against the synced rosters: a callsign resolves to a roster,
    and the player is either on it or he is not.
    """
    players = cctx.get("players") or {}
    callsigns = cctx.get("callsigns") or {}
    public = cctx.get("public_names") or {}
    if not players or not (callsigns or public):
        return []
    team_lookup = {re.sub(r"\s*\([^)]*\)\s*$", "", nm).strip().lower(): rid
                   for rid, nm in public.items()}
    out = []
    for s in sections:
        text = s.get("content_md") or ""
        for name, info in players.items():
            for m in _word_re(name).finditer(text):
                # the attribution sits in the player's own sentence; a team
                # name opening the next sentence is a new subject
                tail = re.split("[.!?;" + chr(10) + "]", text[m.end():m.end() + 70], 1)[0]
                tag = _TAG_RE.search(tail)
                claimed = None
                if tag:
                    for tok in re.split(r"[/,]", tag.group(1)):
                        tok = tok.strip()
                        if tok in callsigns:
                            claimed = callsigns[tok]
                            break
                if claimed is None:
                    low = tail.lower()
                    for team_name, rid in team_lookup.items():
                        if team_name and team_name in low:
                            claimed = rid
                            break
                if claimed is None or claimed == info["rid"]:
                    continue
                out.append(_finding(
                    "identity", "Player attributed to the wrong roster",
                    f"{name} is attributed to {public.get(claimed, claimed)} here, "
                    f"but the synced roster has him on {public.get(info['rid'], info['rid'])}.",
                    s["module_key"], excerpt=_snippet(text, m.start(), m.end() + len(tail.split(')')[0]) + 1),
                    evidence=[f"synced roster: {public.get(info['rid'])}",
                              f"copy says: {public.get(claimed)}"]))
                break
    return out


# ------------------------------------------------------------- freshness

def unavailable_players_cited(sections: list[dict], cctx: dict) -> list[dict]:
    """A player carrying an Out/IR/NA designation, discussed as if he plays.

    Skipped when the paragraph already knows -- it says out, injured, IR,
    or the like -- because the Commissioner writing about an injury is the
    point, not the defect.
    """
    players = cctx.get("players") or {}
    public = cctx.get("public_names") or {}
    out = []
    for s in sections:
        text = s.get("content_md") or ""
        for para in _paragraphs(text):
            if _INJURY_WORDS_RE.search(para):
                continue
            for name, info in players.items():
                status = info.get("status")
                if status not in OUT_STATUSES:
                    continue
                m = _word_re(name).search(para)
                if not m:
                    continue
                out.append(_finding(
                    "freshness", "Player with an out-type designation is written up as available",
                    f"{name} carries '{status}' on the synced roster of "
                    f"{public.get(info['rid'], info['rid'])}; this paragraph does not "
                    f"mention it.",
                    s["module_key"], excerpt=_snippet(para, m.start(), m.end()),
                    evidence=[f"injury_status: {status}",
                              f"roster: {public.get(info['rid'])}"]))
    return out


# ------------------------------------------------------------- coherence

def rank_claim_conflicts(rank_claims: list[dict], cctx: dict) -> list[dict]:
    """Two sections that disagree about where a room ranks.

    `rank_claims` rows: {module_key, rid, pos, claimed}. The freshness check
    already compares each claim against today's data; this compares the
    sections against each other, which is the reader's experience.
    """
    public = cctx.get("public_names") or {}
    by_key: dict[tuple[int, str], dict[int, set[str]]] = defaultdict(lambda: defaultdict(set))
    for c in rank_claims or []:
        by_key[(c["rid"], c["pos"])][c["claimed"]].add(c["module_key"])
    out = []
    for (rid, pos), claims in sorted(by_key.items()):
        if len(claims) < 2:
            continue
        parts = "; ".join(f"#{n} in {', '.join(sorted(mods))}" for n, mods in sorted(claims.items()))
        out.append(_finding(
            "coherence", "Sections disagree about a positional rank",
            f"{public.get(rid, rid)}'s {pos} room is ranked {parts}. One of them "
            f"was written from older data, or one of them is wrong.",
            sorted(next(iter(claims.values())))[0],
            evidence=[f"{public.get(rid, rid)} · {pos}: {parts}"]))
    return out


def subject_saturation(sections: list[dict], cctx: dict) -> list[dict]:
    """One player written up in three or more sections.

    Sometimes that is the week's story and belongs everywhere. Sometimes it
    is the same fact told four times because four briefs surfaced it. The
    finding names the sections so the Commissioner can tell which.
    """
    players = cctx.get("players") or {}
    seen: dict[str, list[str]] = defaultdict(list)
    for s in sections:
        text = s.get("content_md") or ""
        for name in players:
            if " " not in name:
                continue
            if _word_re(name).search(text):
                seen[name].append(s["module_key"])
    out = []
    for name, mods in sorted(seen.items()):
        mods = sorted(set(mods))
        if len(mods) < SATURATION_SECTIONS:
            continue
        out.append(_finding(
            "coherence", "One player carries several sections",
            f"{name} appears in {len(mods)} sections: {', '.join(mods)}. Fine if he "
            f"is the week's story; worth a look if each section is telling the "
            f"same fact.",
            mods[0], evidence=[f"sections: {', '.join(mods)}"]))
    return out


def cross_league_duplicates(sections: list[dict], cctx: dict) -> list[dict]:
    """A paragraph that also appears in the other league's issue.

    Both leagues are researched from the same national sources, so a
    generic paragraph about a player can land in both papers word for
    word. A reader in both leagues notices. Same-league memory stays in its
    league; identical prose across them is the thing the voice profile
    bans.
    """
    other = cctx.get("other_league_paragraphs") or set()
    label = cctx.get("other_league_label") or "the other league"
    if not other:
        return []
    out = []
    for s in sections:
        key = s.get("module_key") or ""
        # His own prose, written once for both papers on purpose: the
        # Lowdown and a special section. The check is for research lanes
        # drafted from national sources, where a shared paragraph is an
        # accident. One finding per section, with the count.
        if key == "lowdown" or key.startswith("custom"):
            continue
        shared = [para for para in _paragraphs(s.get("content_md") or "")
                  if len(normalize_paragraph(para).split()) >= DUPLICATE_MIN_WORDS
                  and normalize_paragraph(para) in other]
        if not shared:
            continue
        out.append(_finding(
            "coherence", "Paragraph also appears in the other league's issue",
            f"{len(shared)} paragraph(s) here are word-for-word in {label}. Two "
            f"leagues, one column: at least one of them should be rewritten for "
            f"its own readers.",
            key, excerpt=shared[0][:140] + ("…" if len(shared[0]) > 140 else ""),
            evidence=[f"{len(shared)} identical after normalisation to {label}"]))
    return out


def check(sections: list[dict], cctx: dict, *, rank_claims: list[dict] | None = None) -> list[dict]:
    """Every coherence finding for an assembled issue, as plain dicts."""
    out: list[dict] = []
    out += format_mismatch(sections, cctx)
    out += stale_team_names(sections, cctx)
    out += owner_attribution(sections, cctx)
    out += unavailable_players_cited(sections, cctx)
    out += rank_claim_conflicts(rank_claims or [], cctx)
    out += subject_saturation(sections, cctx)
    out += cross_league_duplicates(sections, cctx)
    return out


def other_league_paragraphs(texts: list[str]) -> set[str]:
    """Normalised paragraphs from the other league, ready for `check`."""
    out = set()
    for text in texts:
        for para in _paragraphs(text or ""):
            norm = normalize_paragraph(para)
            if len(norm.split()) >= DUPLICATE_MIN_WORDS:
                out.add(norm)
    return out


def callsigns_from_names(public_names: dict[int, str]) -> dict[str, int]:
    """'Wild SeeKats (Seebass/Kats)' -> {'Seebass': 7, 'Kats': 7}."""
    out: dict[str, int] = {}
    for rid, nm in (public_names or {}).items():
        m = re.search(r"\(([^()]+)\)\s*$", nm or "")
        if not m:
            continue
        for tok in re.split(r"[/,]", m.group(1)):
            tok = tok.strip()
            if tok:
                out[tok] = rid
    return out
