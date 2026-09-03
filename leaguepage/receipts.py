"""Receipts — old claims meeting new evidence.

Sleeper cannot do this. It has no memory of what anybody said in August.
League Page has every published issue frozen under `published/`, so when a
roster moves it can go back and find the sentence that is now under
pressure, quote it verbatim, and say what changed.

Three rules keep this from becoming a machine that dunks on the
Commissioner with a two-game sample:

1. **A claim is quoted, never paraphrased.** The receipt shows his sentence
   as he wrote it, with the issue it came from.
2. **A status is evidence, not a verdict.** "Aging well" and "Under
   pressure" describe what the data now says about the premise. Nothing here
   ever says a take was wrong.
3. **Small samples produce nothing.** Positional claims need played weeks
   behind them. The one thing testable with zero games is whether a player
   the claim rested on is still on that roster — a fact, not an opinion.

Repetition suppression is real, not theoretical: each public surfacing is
recorded per week, and a receipt that led the front page recently steps
aside for the next one.
"""
from __future__ import annotations

import hashlib
import json
import re

from leaguepage.config import League
from leaguepage.draft_value import SPECIAL_TEAMS
from leaguepage.storage import Storage

AGING_WELL = "Aging well"
UNDER_PRESSURE = "Under pressure"
TOO_EARLY = "Too early"

MIN_WEEKS_FOR_POSITION_CLAIMS = 3   # below this a room's rank is draft-day noise
REPEAT_COOLDOWN_WEEKS = 2           # weeks a surfaced receipt sits out

# Words that turn a sentence about a roster into a claim about the season.
# Deliberately narrow: an assertion has to sound like one.
_CLAIM_WORDS = re.compile(
    r"\b(assumption|assumptions|assume|assumed|assuming|bet|bets|gamble|risk|"
    r"risky|conviction|premium|thin|problem|weakness|carries|carry|depends|"
    r"hinges|if it hits|can break|will break|can sink|the question|expensive|"
    r"priced|punted|optional|divest\w*|high variance|flammable|concentrat\w+)\b",
    re.I)
_POSITION_RE = re.compile(r"\b(QB|RB|WR|TE|quarterback|running back|wide receiver|"
                          r"tight end)\b", re.I)
# A claim about a kicker or defense "premium" measures the reference board's
# shape, not a roster decision -- the calibration rule the rest of the
# codebase already enforces, and the exact sentence the published Surfeit
# correction was issued to stop headlining. `candidate_takes` blocked these
# at capture time; the archive-derived path had no guard, so one ordinary
# kicker drop could put one back on the front page at full weight.
_SPECIAL_TEAMS_CLAIM_RE = re.compile(
    r"\bkicker|\bkickers\b|\bdefense[s]?\b|\bDST\b|\bD/ST\b", re.I)
_ST_MARKET_RE = re.compile(r"premium|reach|early|tax", re.I)
_POS_CANON = {"quarterback": "QB", "running back": "RB",
              "wide receiver": "WR", "tight end": "TE"}
_SENTENCE_RE = re.compile(r"[^.!?]*[.!?]")


_TABLE_ROW_RE = re.compile(r"^\s*\|.*$|^.*\|.*\|.*$", re.M)


def _sentences(text: str) -> list[str]:
    """Prose sentences only.

    Headings and markdown table rows are stripped first: a table row reads
    as a sentence to a regex and produces quotes like "0 | | 3 | Statistical
    Anomalies | A- | 139." — technically a match, editorially worthless."""
    body = re.sub(r"^#{1,6}\s.*$", " ", text, flags=re.M)
    body = _TABLE_ROW_RE.sub(" ", body)
    flat = re.sub(r"\s+", " ", body)
    return [s.strip() for s in _SENTENCE_RE.findall(flat) if len(s.strip()) > 30]


def _claim_id(league_slug: str, issue_key: str, quote: str) -> str:
    raw = f"{league_slug}|{issue_key}|{quote}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def _distinctive_tokens(name_tokens: dict[int, set[str]]) -> dict[str, int]:
    """token -> roster_id, for tokens that identify exactly one team.

    "Bandidos" names Los Bandidos and nothing else; "Statistical" would too.
    Shared tokens ("Team", "One") identify nobody and are dropped, so an
    inline mention can never be attributed to the wrong roster."""
    counts: dict[str, int] = {}
    for toks in name_tokens.values():
        for t in toks:
            counts[t] = counts.get(t, 0) + 1
    return {t: rid for rid, toks in name_tokens.items() for t in toks
            if counts[t] == 1 and len(t) >= 4}


def _surname_index(player_positions: dict[str, str]) -> dict[str, str]:
    """surname -> full name, for surnames that belong to exactly one player.

    People write "Bates at minus-91", not "Jake Bates at minus-91". Team
    defenses are excluded: their "surname" is a city nickname, so "three
    Bills" (three Buffalo players) would otherwise resolve to the Buffalo
    defense and manufacture a receipt out of nothing."""
    by_last: dict[str, set[str]] = {}
    for full, pos in player_positions.items():
        if (pos or "").upper() in ("DEF", "DST"):
            continue
        parts = full.split()
        if len(parts) >= 2 and len(parts[-1]) >= 4:
            by_last.setdefault(parts[-1], set()).add(full)
    return {last: next(iter(names)) for last, names in by_last.items()
            if len(names) == 1}


def extract_claims(snaps: list[dict], league_slug: str,
                   name_tokens: dict[int, set[str]],
                   player_positions: dict[str, str]) -> list[dict]:
    """Testable assertions from published issues, with provenance attached.

    A sentence qualifies when it belongs to a team — either sitting under a
    heading that resolves to one, or naming one inline, which is how the
    Lowdown does it — reads like an assertion, and mentions something
    measurable: a skill position or a named player."""
    from leaguepage.pubqa import _team_blocks, QAContext

    ctx = QAContext(league_slug=league_slug, season="", issue_key="",
                    n_teams=len(name_tokens))
    ctx.name_tokens = name_tokens
    ctx.public_names = {rid: "" for rid in name_tokens}
    distinctive = _distinctive_tokens(name_tokens)
    surnames = _surname_index(player_positions)

    def _players_in(sentence: str) -> set[str]:
        found = {p for p in player_positions
                 if re.search(rf"\b{re.escape(p)}\b", sentence)}
        for last, full in surnames.items():
            if full not in found and re.search(rf"\b{re.escape(last)}\b", sentence):
                found.add(full)
        return found

    def _team_in(sentence: str) -> int | None:
        hits = {rid for tok, rid in distinctive.items()
                if re.search(rf"\b{re.escape(tok)}\b", sentence, re.I)}
        return hits.pop() if len(hits) == 1 else None

    claims: list[dict] = []
    seen: set[str] = set()
    for snap in snaps:
        for section in snap.get("sections", []):
            text = section.get("content_md") or ""
            # A claim belongs to whichever team's heading block it sits in;
            # anything outside a block (the Lowdown is all outside) is
            # attributed by the team named inside the sentence itself.
            scoped = [(re.sub(r"\s+", " ", body), rid)
                      for _h, body, rid in _team_blocks(text, ctx)]
            for sentence in _sentences(text):
                if not _CLAIM_WORDS.search(sentence):
                    continue
                rid = next((block_rid for body, block_rid in scoped
                            if sentence in body), None)
                if rid is None:
                    rid = _team_in(sentence)
                if rid is None:
                    continue
                positions = {
                    _POS_CANON.get(m.group(1).lower(), m.group(1).upper())
                    for m in _POSITION_RE.finditer(sentence)}
                players = _players_in(sentence)
                if not positions and not players:
                    continue
                skill = [p for p in players
                         if (player_positions.get(p) or "").upper()
                         not in SPECIAL_TEAMS]
                if not skill and not positions:
                    # named nobody but special-teams players
                    continue
                if (not skill and _SPECIAL_TEAMS_CLAIM_RE.search(sentence)
                        and _ST_MARKET_RE.search(sentence)):
                    continue
                cid = _claim_id(league_slug, snap["issue_key"], sentence)
                if cid in seen:
                    continue
                seen.add(cid)
                claims.append({
                    "claim_id": cid,
                    "roster_id": rid,
                    "quote": sentence,
                    "positions": sorted(positions),
                    # Special-teams players are recorded, because the claim
                    # named them, but only skill players can carry a verdict.
                    "players": sorted(players),
                    "judgeable": sorted(skill),
                    "issue_key": snap["issue_key"],
                    "issue_label": snap["issue_label"],
                    "href": snap["href"],
                    "section_title": section.get("title") or "",
                })
    return claims


def evaluate(claims: list[dict], *, rosters: dict[int, set[str]],
             positional_ranks: dict[int, dict[str, int]], n_teams: int,
             weeks_played: int, names: dict[int, str]) -> list[dict]:
    """Attach a status and the evidence behind it. Claims with nothing to
    say are returned as TOO_EARLY and callers drop them."""
    out = []
    for c in claims:
        rid = c["roster_id"]
        status, note, weight = TOO_EARLY, None, 0

        judgeable = c.get("judgeable")
        if judgeable is None:            # claims stored before the split
            judgeable = c["players"]
        gone = sorted(p for p in judgeable if p not in rosters.get(rid, set()))
        if gone:
            status = UNDER_PRESSURE
            note = (f"{', '.join(gone)} "
                    + ("is" if len(gone) == 1 else "are")
                    + f" no longer on {names.get(rid, 'that roster')}'s roster.")
            weight = 82
        elif weeks_played >= MIN_WEEKS_FOR_POSITION_CLAIMS and c["positions"]:
            ranks = positional_ranks.get(rid) or {}
            graded = [(p, ranks[p]) for p in c["positions"] if p in ranks]
            if graded:
                pos, rank = min(graded, key=lambda pr: pr[1])
                bottom = rank >= round(0.75 * n_teams)
                worry = bool(re.search(r"thin|problem|weakness|risk|break|sink",
                                       c["quote"], re.I))
                if worry and bottom:
                    status, weight = AGING_WELL, 80
                    note = (f"{pos} still ranks {rank} of {n_teams} after "
                            f"{weeks_played} weeks.")
                elif worry and rank <= round(0.4 * n_teams):
                    status, weight = UNDER_PRESSURE, 78
                    note = (f"{pos} now ranks {rank} of {n_teams} — the room the "
                            "claim worried about is holding up.")
                elif not worry and bottom:
                    status, weight = UNDER_PRESSURE, 74
                    note = f"{pos} ranks {rank} of {n_teams} after {weeks_played} weeks."
        out.append({**c, "status": status, "status_note": note, "weight": weight,
                    "team": names.get(rid, f"Roster {rid}")})
    return out


# ------------------------------------------------------ repetition memory

def _shown_key(league_slug: str, season: str) -> str:
    return f"receipts_shown:{league_slug}:{season}"


def shown_weeks(storage: Storage, league_slug: str, season: str) -> dict[str, list[int]]:
    raw = storage.get_meta(_shown_key(league_slug, season))
    try:
        return json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}


def record_shown(storage: Storage, league_slug: str, season: str,
                 claim_id: str, week: int) -> None:
    """Remember that this receipt was on a public page in this week.

    Keyed by week, so rebuilding the same week is idempotent — the point is
    that the same receipt does not lead two weeks running."""
    log = shown_weeks(storage, league_slug, season)
    weeks = set(log.get(claim_id, []))
    weeks.add(int(week))
    log[claim_id] = sorted(weeks)
    storage.set_meta(_shown_key(league_slug, season), json.dumps(log))


def _recently_shown(log: dict[str, list[int]], claim_id: str, week: int) -> bool:
    return any(week - w < REPEAT_COOLDOWN_WEEKS and w != week
               for w in log.get(claim_id, []))


# ---------------------------------------------------------- entry points


def live_receipts(storage: Storage, league: League, season: str, week: int,
                  snaps: list[dict], names: dict[int, dict]) -> list[dict]:
    """Every receipt currently worth showing, strongest first."""
    from leaguepage.matchup_analysis import weekly_scores
    from leaguepage.pubqa import _norm_tokens
    from leaguepage.team_analytics import positional_profile

    public = {rid: v["name"] for rid, v in names.items() if v.get("name")}
    # Snapshots arrive for every season on file, and a claim is graded
    # against the rosters as they stand today. Every player moves between
    # seasons, so on the first sync of a new year every claim from the old
    # one resolves at once and the front page leads with a receipt about a
    # roster that no longer exists.
    snaps = [s for s in snaps if s.get("season") == season]
    if not public or not snaps:
        return []
    rosters: dict[int, set[str]] = {}
    player_positions: dict[str, str] = {}
    for r in storage.get_rosters(league.league_id):
        held = set()
        for pid in (r.get("players") or []):
            p = storage.get_player(pid) or {}
            nm = p.get("full_name")
            if nm:
                held.add(nm)
                player_positions.setdefault(nm, (p.get("position") or "").upper())
        rosters[r["roster_id"]] = held
    drafts = storage.get_drafts_for_league(league.league_id)
    if drafts:
        for p in storage.get_draft_picks(drafts[0]["draft_id"]):
            meta = p.get("metadata") or {}
            nm = " ".join(x for x in (meta.get("first_name"), meta.get("last_name")) if x).strip()
            if nm:
                player_positions.setdefault(nm, (meta.get("position") or "").upper())

    scores = weekly_scores(storage, league.league_id, 18)
    weeks_played = max((len(v) for v in scores.values()), default=0)
    profile = positional_profile(storage, league, weeks_played=weeks_played)
    ranks: dict[int, dict[str, int]] = {}
    for pos in profile["positions"]:
        for rid, rank in profile["ranks"][pos].items():
            ranks.setdefault(rid, {})[pos] = rank

    claims = extract_claims(snaps, league.slug,
                            {rid: _norm_tokens(nm) for rid, nm in public.items()},
                            player_positions)
    graded = evaluate(claims, rosters=rosters, positional_ranks=ranks,
                      n_teams=profile["n"], weeks_played=weeks_played,
                      names=public)
    live = [r for r in graded if r["status"] != TOO_EARLY]
    log = shown_weeks(storage, league.slug, season)
    live.sort(key=lambda r: (_recently_shown(log, r["claim_id"], week), -r["weight"]))
    return live


def front_page_receipt(storage: Storage, league: League, season: str, week: int,
                       snaps: list[dict], names: dict[int, dict]) -> dict | None:
    """The one receipt the front page carries, or None.

    Structured Takes come first: those are claims the Commissioner
    deliberately marked and cleared, which is a stronger thing to print than
    a sentence a regex noticed. The archive-derived heuristic is the
    fallback for weeks when no tracked take has moved."""
    from leaguepage import takes as takes_mod

    public = {rid: v["name"] or f"Roster {rid}" for rid, v in names.items()}
    log = shown_weeks(storage, league.slug, season)
    tracked = [r for r in takes_mod.public_receipts(storage, league, season, public)
               if not _recently_shown(log, f"take:{r['take_id']}", week)]
    if tracked:
        r = tracked[0]
        record_shown(storage, league.slug, season, f"take:{r['take_id']}", week)
        where = ("he wrote this in the " + r["issue_label"] if r["verbatim"]
                 else "he wrote this, in substance, in the " + r["issue_label"])
        return {
            "claim": (f"“{r['quote']}”" if r["verbatim"] else r["quote"]),
            "status": r["status"],
            "status_note": f"{r['evidence'][0]} — {where}.",
            "href": r["href"],
            "cta": r["issue_label"],
            "weight": 90 if r["settled"] else 84,
            "claim_id": f"take:{r['take_id']}",
            "verbatim": r["verbatim"],
        }

    live = live_receipts(storage, league, season, week, snaps, names)
    if not live:
        return None
    r = live[0]
    record_shown(storage, league.slug, season, r["claim_id"], week)
    return {
        "claim": f"“{r['quote'].strip()}”",
        "status": r["status"],
        "status_note": (f"{r['status_note']} — from {r['issue_label']}, "
                        f"{r['section_title']}."),
        "href": r["href"],
        "cta": r["issue_label"],
        "weight": r["weight"],
        "claim_id": r["claim_id"],
    }


def receipts_for_team(storage: Storage, league: League, season: str, week: int,
                      snaps: list[dict], names: dict[int, dict], rid: int,
                      *, limit: int = 2) -> list[dict]:
    """Tracked takes about this team first, archive-derived ones after."""
    from leaguepage import takes as takes_mod

    public = {r_: v["name"] or f"Roster {r_}" for r_, v in names.items()}
    out = []
    for r in takes_mod.public_receipts(storage, league, season, public):
        if r["subject_roster_id"] != rid:
            continue
        out.append({
            "roster_id": rid, "quote": r["quote"], "verbatim": r["verbatim"],
            "status": r["status"], "status_note": r["evidence"][0],
            "href": r["href"], "issue_label": r["issue_label"],
            "team": public.get(rid, ""), "tracked": True,
        })
    for r in live_receipts(storage, league, season, week, snaps, names):
        if r["roster_id"] == rid:
            out.append({**r, "verbatim": True, "tracked": False})
    return out[:limit]


def receipts_for_matchup(storage: Storage, league: League, season: str,
                         week: int, names: dict[int, dict],
                         roster_ids: list[int]) -> dict | None:
    """One excellent past statement for a matchup, or nothing.

    Deliberately capped at one. A matchup card carrying three old quotes is
    a scrapbook, not a callback."""
    from leaguepage import takes as takes_mod

    public = {r_: v["name"] or f"Roster {r_}" for r_, v in names.items()}
    log = shown_weeks(storage, league.slug, season)
    best = None
    for r in takes_mod.public_receipts(storage, league, season, public):
        if r["subject_roster_id"] not in roster_ids:
            continue
        if _recently_shown(log, f"take:{r['take_id']}", week):
            continue
        best = r
        break
    if best is None:
        return None
    record_shown(storage, league.slug, season, f"take:{best['take_id']}", week)
    return {
        "quote": best["quote"], "verbatim": best["verbatim"],
        "attribution": best["attribution"], "when": best["when"],
        "status": best["status"], "evidence": best["evidence"],
        "href": best["href"], "issue_label": best["issue_label"],
        "subject_name": best["subject_name"],
    }
