"""Takes — claims the Commissioner chose to be held to.

A Take is not a sentence a machine decided was a prediction. It is a
sentence he marked, because the whole entertainment value of a receipt comes
from someone having stuck their neck out on purpose. The system's job is to
remember it, watch for evidence, and hand it back at the right moment.

Three rules shape everything here:

1. **He decides what counts.** Nothing becomes a Take automatically, not
   even from the conservative candidate scan. The engine proposes; he tracks.
2. **The engine recommends, he rules.** `recommended_status` and `status`
   are different columns. A take the engine leans against stays exactly
   where he left it until he moves it, and the disagreement is visible.
3. **A verdict costs evidence.** Every status the engine proposes carries
   the lines that justify it, computed from synced data. There is no score,
   no model, and nothing to take on faith. Below the horizon or below the
   sample floor the answer is TOO EARLY, which is a real answer.

The tone rule is the same one the rest of the deterministic layer follows:
evidence, never punchlines. "Evidence is moving against this take" is the
strongest thing this module will ever say. The joke is his to make.
"""
from __future__ import annotations

import re

from leaguepage.config import League
from leaguepage.draft_value import SKILL_POSITIONS
from leaguepage.storage import Storage

OPEN = "open"
TOO_EARLY = "too_early"
LEANING_RIGHT = "leaning_right"
LEANING_WRONG = "leaning_wrong"
RESOLVED_RIGHT = "resolved_right"
RESOLVED_WRONG = "resolved_wrong"
VOID = "void"

STATUS_LABELS = {
    OPEN: "Open",
    TOO_EARLY: "Too early",
    LEANING_RIGHT: "Leaning right",
    LEANING_WRONG: "Leaning wrong",
    RESOLVED_RIGHT: "Resolved right",
    RESOLVED_WRONG: "Resolved wrong",
    VOID: "Void",
}
# What a reader sees. Deliberately blunter than the internal vocabulary and
# still not a joke — the Commissioner supplies those in his own copy.
PUBLIC_STATUS = {
    LEANING_RIGHT: "AGING WELL",
    RESOLVED_RIGHT: "AGING WELL",
    LEANING_WRONG: "UNDER PRESSURE",
    RESOLVED_WRONG: "BUSTED",
}

TOPICS = ("draft", "roster", "matchup", "trade", "playoff", "power", "other")
SUBJECT_TYPES = ("team", "player", "matchup", "league")

# Horizon -> weeks to wait before a verdict is even considered. `manual`
# means the Commissioner will say when.
HORIZONS = {
    "next-week": 1,
    "3-weeks": 3,
    "midseason": None,      # resolved against the league's own midpoint
    "end-of-season": None,
    "playoffs": None,
    "manual": None,
}
HORIZON_LABELS = {
    "next-week": "Next week", "3-weeks": "Three weeks",
    "midseason": "Midseason", "end-of-season": "End of season",
    "playoffs": "Playoffs", "manual": "Manual",
}

# Sample floors per topic, below which no verdict is offered at all. A
# positional room ranked from two games is draft-day noise wearing a number.
MIN_WEEKS = {
    "roster": 3, "power": 4, "draft": 3, "trade": 3,
    "playoff": 6, "matchup": 1, "other": 4,
}


# ------------------------------------------------------------- inference

_TOPIC_PATTERNS = [
    ("matchup", r"\b(beat|beats|win this week|should win|lose to|upset|"
                r"this matchup|head to head)\b"),
    ("trade", r"\b(trade|traded|trading|waiver|FAAB|claim|claimed|pick(?:ed)? up|"
              r"dropp?(?:ed|ing))\b"),
    ("playoff", r"\b(playoff|playoffs|postseason|seed|berth|bye|miss the "
                r"(?:playoffs|dance)|make the (?:playoffs|dance))\b"),
    ("draft", r"\b(draft|drafted|draft night|pick \d+|round \d+|reach|steal|"
              r"consensus|ADP|board)\b"),
    ("roster", r"\b(QB|RB|WR|TE|room|depth|starters|bench|roster|thin|"
               r"quarterback|running back|wide receiver|tight end)\b"),
    ("power", r"\b(best team|worst team|contender|favou?rite|title|championship|"
              r"top of the (?:table|league)|strongest|weakest)\b"),
]


def infer_topic(quote: str) -> str:
    """Best-guess topic, in priority order. Always correctable by hand —
    this exists to save typing, not to be authoritative."""
    for topic, pattern in _TOPIC_PATTERNS:
        if re.search(pattern, quote, re.I):
            return topic
    return "other"


def infer_subject(quote: str, *, name_tokens: dict[int, set[str]],
                  public_names: dict[int, str],
                  slugs: dict[int, str]) -> dict:
    """Which team the claim is about, when exactly one is named.

    Reuses the distinctive-token index the receipts layer already relies on:
    a token that identifies exactly one roster. Two teams named means a
    matchup claim and no single subject; zero means the Commissioner picks."""
    from leaguepage.receipts import _distinctive_tokens

    hits = {rid for tok, rid in _distinctive_tokens(name_tokens).items()
            if re.search(rf"\b{re.escape(tok)}\b", quote, re.I)}
    if len(hits) == 1:
        rid = hits.pop()
        return {"subject_type": "team", "subject_roster_id": rid,
                "subject": slugs.get(rid), "subject_name": public_names.get(rid)}
    if len(hits) == 2:
        return {"subject_type": "matchup", "subject_roster_id": None,
                "subject": None,
                "subject_name": " vs ".join(
                    sorted(public_names.get(r, f"Roster {r}") for r in hits))}
    return {"subject_type": None, "subject_roster_id": None,
            "subject": None, "subject_name": None}


def infer_players(quote: str, player_positions: dict[str, str]) -> list[str]:
    """Players named in the claim, full names and unique surnames alike."""
    from leaguepage.receipts import _surname_index

    found = {p for p in player_positions
             if re.search(rf"\b{re.escape(p)}\b", quote)}
    for last, full in _surname_index(player_positions).items():
        if full not in found and re.search(rf"\b{re.escape(last)}\b", quote):
            found.add(full)
    return sorted(found)


def subject_from_heading(quote: str, section_md: str, *,
                         name_tokens: dict[int, set[str]]) -> int | None:
    """The roster whose heading block this sentence sits under, if any.

    A per-team capsule names its team once, in the heading. Any client that
    posts a quote without a subject gets the same attribution the candidate
    scan would have made."""
    from leaguepage.pubqa import QAContext, _team_blocks

    ctx = QAContext(league_slug="", season="", issue_key="",
                    n_teams=len(name_tokens))
    ctx.name_tokens = name_tokens
    ctx.public_names = {rid: "" for rid in name_tokens}
    flat_quote = " ".join(quote.split())
    for _heading, body, rid in _team_blocks(section_md, ctx):
        if flat_quote in " ".join(body.split()):
            return rid
    return None


def review_week_for(horizon: str | None, *, created_week: int | None,
                    playoff_week_start: int | None) -> int | None:
    """The first week a verdict may be considered. None = the Commissioner
    will decide when, which is what `manual` means."""
    base = created_week or 0
    start = int(playoff_week_start or 15)
    if horizon == "next-week":
        return base + 1
    if horizon == "3-weeks":
        return base + 3
    if horizon == "midseason":
        return max(1, start // 2)
    if horizon in ("end-of-season", "playoffs"):
        return start
    return None


# ------------------------------------------------------------- creation


def create_take(storage: Storage, league: League, season: str, *,
                quote: str, issue_key: str, section: str,
                week: int | None = None, topic: str | None = None,
                subject_type: str | None = None,
                subject_roster_id: int | None = None,
                subject: str | None = None, subject_name: str | None = None,
                confidence: str | None = None, review_after: str | None = None,
                verbatim: bool = True, href: str | None = None,
                note: str | None = None, players: list[str] | None = None,
                playoff_week_start: int | None = None) -> int:
    """Track one take. Metadata the Commissioner did not supply is inferred
    where it is safe to and left empty where it is not."""
    quote = (quote or "").strip()
    if not quote:
        raise ValueError("A take needs a quote.")
    topic = topic or infer_topic(quote)
    return storage.add_take(
        league_slug=league.slug, season=season, week=week,
        context=issue_key, source=section, subject=subject or "",
        quote=quote, players=players, topic=topic, confidence=confidence,
        issue_key=issue_key, subject_type=subject_type,
        subject_name=subject_name, subject_roster_id=subject_roster_id,
        review_after=review_after,
        review_week=review_week_for(review_after, created_week=week,
                                    playoff_week_start=playoff_week_start),
        verbatim=verbatim, href=href, note=note)


# ----------------------------------------------------------- evaluation


def _fmt(n: float) -> str:
    return f"{n:g}"


def _roster_evidence(ctx, take) -> tuple[str | None, list[str]]:
    """Positional claims: has the room the claim worried about moved?"""
    rid = take.get("subject_roster_id")
    ranks = (ctx["positional_ranks"].get(rid) or {}) if rid else {}
    if not ranks:
        return None, []
    named = [p for p in SKILL_POSITIONS
             if re.search(rf"\b{p}\b", take["quote"], re.I)]
    if not named:
        named = [min(ranks, key=ranks.get)] if ranks else []
    n = ctx["n_teams"]
    lines, direction = [], None
    worried = bool(re.search(r"thin|problem|weak|risk|break|sink|sunk|cost|"
                             r"nothing behind|depth", take["quote"], re.I))
    for pos in named:
        rank = ranks.get(pos)
        if rank is None:
            continue
        was = (ctx["opening_ranks"].get(rid) or {}).get(pos)
        moved = f" (was #{was} at publication)" if was and was != rank else ""
        lines.append(f"{pos} room now ranks #{rank} of {n}{moved}")
        bottom, top = rank >= round(0.75 * n), rank <= round(0.4 * n)
        if worried and bottom:
            direction = LEANING_RIGHT
        elif worried and top:
            direction = LEANING_WRONG
        elif not worried and bottom:
            direction = LEANING_WRONG
        elif not worried and top:
            direction = LEANING_RIGHT
    return direction, lines


def _power_evidence(ctx, take) -> tuple[str | None, list[str]]:
    """Team-strength claims: record, scoring, all-play, model rank."""
    rid = take.get("subject_roster_id")
    if not rid:
        return None, []
    rec = ctx["records"].get(rid)
    ap = ctx["all_play"].get(rid)
    model = ctx["model_rank"].get(rid)
    n = ctx["n_teams"]
    lines = []
    if rec:
        lines.append(f"record {rec['wins']}-{rec['losses']}, "
                     f"{_fmt(rec['fpts'])} points for")
    if ap:
        lines.append(f"all-play {ap['wins']}-{ap['losses']} "
                     "(what the record would be against everybody)")
    if model:
        lines.append(f"model board #{model} of {n}")
    if model is None:
        return None, lines
    claims_strong = bool(re.search(r"best|contender|title|championship|strongest|"
                                   r"top of the", take["quote"], re.I))
    claims_weak = bool(re.search(r"worst|weakest|bottom|sink|doomed|"
                                 r"basement", take["quote"], re.I))
    if claims_strong:
        return (LEANING_RIGHT if model <= round(0.3 * n)
                else LEANING_WRONG if model >= round(0.6 * n) else None), lines
    if claims_weak:
        return (LEANING_RIGHT if model >= round(0.7 * n)
                else LEANING_WRONG if model <= round(0.4 * n) else None), lines
    return None, lines


def _draft_evidence(ctx, take) -> tuple[str | None, list[str]]:
    """Draft claims. Never re-classifies a pick: REACH/STEAL compares one
    selection with the board and is immutable market analysis. What is
    testable is whether the player is still here and playing."""
    lines, gone, kept = [], [], []
    # Kickers start every week by definition, so "did he start" says nothing
    # about a claim. Special-teams players are reported but never carry the
    # verdict — the same calibration rule the rest of the codebase follows.
    positions = ctx.get("player_positions") or {}
    named = take.get("players") or []
    skill = [n for n in named
             if (positions.get(n) or "") not in ("K", "DEF", "DST")]
    judgeable = skill or []
    for name in named:
        rid = ctx["roster_of_player"].get(name)
        if rid is None:
            if name in judgeable:
                gone.append(name)
            lines.append(f"{name} is not on any roster in this league now")
            continue
        if name in judgeable:
            kept.append(name)
        starts = ctx["starts"].get(name)
        pts = ctx["points"].get(name)
        held = ctx["public_names"].get(rid, f"Roster {rid}")
        detail = f"{name} is on {held}"
        if starts is not None:
            detail += f", started {starts} of {ctx['weeks_played']} weeks"
        if pts is not None:
            detail += f", {_fmt(pts)} points"
        lines.append(detail)
    if not lines:
        return None, []
    if not judgeable:
        # Only kickers and defenses were named; there is nothing here that a
        # roster decision can be held to.
        return None, lines
    praised = bool(re.search(r"steal|value|bargain|fell to|best pick|"
                             r"crusher|hit", take["quote"], re.I))
    doubted = bool(re.search(r"reach|bust|too early|risky|questionable|"
                             r"will regret|overpaid", take["quote"], re.I))
    if gone and not kept:
        return (LEANING_WRONG if praised else LEANING_RIGHT if doubted else None), lines
    started = [n for n in kept if (ctx["starts"].get(n) or 0) > 0]
    if kept and ctx["weeks_played"] >= MIN_WEEKS["draft"]:
        if praised:
            return (LEANING_RIGHT if started else LEANING_WRONG), lines
        if doubted:
            return (LEANING_WRONG if started else LEANING_RIGHT), lines
    return None, lines


def _trade_evidence(ctx, take) -> tuple[str | None, list[str]]:
    """Transaction claims read the ledger the transaction layer already
    keeps: the rank shift at the time and what happened since."""
    rid = take.get("subject_roster_id")
    moves = ctx["moves"].get(rid) or []
    if not moves:
        return None, []
    lines = []
    for m in moves[-2:]:
        line = m["line"]
        if m.get("rank_shift"):
            line += f" — {m['rank_shift']}"
        if m.get("outcome"):
            line += f"; {m['outcome']}"
        if m.get("questionable"):
            line += " (flagged questionable at the time)"
        lines.append(line)
    improved = any("→" in (m.get("rank_shift") or "") and
                   _shift_improved(m["rank_shift"]) for m in moves)
    claims_good = bool(re.search(r"fix|fixes|solved|solves|upgrade|improve|"
                                 r"better|steal", take["quote"], re.I))
    if ctx["weeks_played"] < MIN_WEEKS["trade"]:
        return None, lines
    if claims_good:
        return (LEANING_RIGHT if improved else LEANING_WRONG), lines
    return None, lines


def _shift_improved(shift: str) -> bool:
    nums = [int(x) for x in re.findall(r"#?(\d+)", shift or "")]
    return len(nums) >= 2 and nums[-1] < nums[0]


def _matchup_evidence(ctx, take) -> tuple[str | None, list[str]]:
    """Matchup predictions resolve on the actual result, immediately."""
    result = ctx["matchup_results"].get(take.get("subject_roster_id"))
    if not result:
        return None, []
    lines = [f"week {result['week']}: {result['line']}"]
    named_winner = take.get("subject_roster_id")
    predicted_win = bool(re.search(r"\b(win|beat|takes? this|should win|"
                                   r"handles?)\b", take["quote"], re.I))
    predicted_loss = bool(re.search(r"\b(lose|loses|drops? this|falls?)\b",
                                    take["quote"], re.I))
    if not (predicted_win or predicted_loss) or named_winner is None:
        return None, lines
    if result.get("tie"):
        # Nobody won, so nobody was wrong. Calling a drawn game BUSTED and
        # quoting him on the front page for it is the kind of confident
        # nonsense that makes a whole feature untrustworthy.
        lines.append("the game was a tie, so the prediction is void")
        return VOID, lines
    won = result["won"]
    right = won if predicted_win else not won
    return (RESOLVED_RIGHT if right else RESOLVED_WRONG), lines


def _playoff_evidence(ctx, take) -> tuple[str | None, list[str]]:
    rid = take.get("subject_roster_id")
    outlook = ctx["playoff"].get(rid)
    if not outlook:
        return None, []
    lines = [f"playoff outlook: {outlook}"]
    if ctx["weeks_played"] < MIN_WEEKS["playoff"]:
        return None, lines
    claims_in = bool(re.search(r"make the|makes the|will make|in the playoffs|"
                               r"contender|seed", take["quote"], re.I))
    claims_out = bool(re.search(r"miss|misses|will not make|won't make|out of it",
                                take["quote"], re.I))
    strong = any(w in outlook.lower() for w in ("lock", "likely", "in"))
    weak = any(w in outlook.lower() for w in ("long shot", "eliminated", "out"))
    if claims_in:
        return (LEANING_RIGHT if strong else LEANING_WRONG if weak else None), lines
    if claims_out:
        return (LEANING_RIGHT if weak else LEANING_WRONG if strong else None), lines
    return None, lines


_HOOKS = {
    "roster": _roster_evidence,
    "power": _power_evidence,
    "draft": _draft_evidence,
    "trade": _trade_evidence,
    "matchup": _matchup_evidence,
    "playoff": _playoff_evidence,
}


def evaluate_take(take: dict, ctx: dict) -> dict:
    """(recommended_status, evidence lines, why) for one take.

    Two gates come before any hook runs, and both answer TOO EARLY rather
    than guessing: the take's own review horizon, and the topic's sample
    floor. A verdict that arrives before the evidence could exist is not a
    verdict."""
    if take.get("status") in (RESOLVED_RIGHT, RESOLVED_WRONG, VOID):
        return {"recommended_status": take["status"], "evidence": [],
                "why": "already settled by the Commissioner"}
    topic = take.get("topic") or "other"
    weeks = ctx["weeks_played"]
    hook = _HOOKS.get(topic)
    _, lines = hook(ctx, take) if hook else (None, [])

    review_week = take.get("review_week")
    if review_week and ctx["week"] < review_week:
        return {"recommended_status": TOO_EARLY, "evidence": lines,
                "why": f"review horizon is week {review_week}; "
                       f"the league is on week {ctx['week']}"}
    floor = MIN_WEEKS.get(topic, MIN_WEEKS["other"])
    if weeks < floor:
        return {"recommended_status": TOO_EARLY, "evidence": lines,
                "why": f"{weeks} week(s) played; {topic} claims need {floor}"}
    if not hook:
        return {"recommended_status": OPEN, "evidence": lines,
                "why": "no deterministic evidence hook for this topic"}
    direction, lines = hook(ctx, take)
    if direction is None:
        return {"recommended_status": OPEN, "evidence": lines,
                "why": "evidence exists but does not point either way yet"}
    return {"recommended_status": direction, "evidence": lines,
            "why": ("evidence supports this take" if direction == LEANING_RIGHT
                    else "evidence is moving against this take"
                    if direction == LEANING_WRONG
                    else "the result is in")}


def evaluation_context(storage: Storage, league: League, season: str,
                       week: int) -> dict:
    """Everything the hooks compare against, computed once per league."""
    from leaguepage.matchup_analysis import all_play, team_record, weekly_scores
    from leaguepage.model_views import model_board
    from leaguepage.team_analytics import (
        playoff_outlook, positional_profile, get_snapshot,
    )
    from leaguepage.team_names import resolve_public_names
    from leaguepage.transaction_analysis import analyze_transactions, describe_move

    names = resolve_public_names(storage, league)
    public_names = {rid: v["name"] or f"Roster {rid}" for rid, v in names.items()}
    scores = weekly_scores(storage, league.league_id, 18)
    weeks_played = max((len(v) for v in scores.values()), default=0)
    profile = positional_profile(storage, league, weeks_played=weeks_played)
    ranks: dict[int, dict[str, int]] = {}
    for pos in profile["positions"]:
        for rid, rank in profile["ranks"][pos].items():
            ranks.setdefault(rid, {})[pos] = rank

    # Where each room stood in the preseason snapshot, so evidence can say
    # "was #5, now #10" instead of only "#10".
    # Snapshots store {position: {roster_id: rank}} and JSON stringifies the
    # roster ids; the hooks want it the other way round.
    opening: dict[int, dict[str, int]] = {}
    snap = get_snapshot(storage, league, season, 0)
    for pos, by_rid in ((snap or {}).get("positional_ranks") or {}).items():
        for rid, rank in (by_rid or {}).items():
            try:
                opening.setdefault(int(rid), {})[pos] = int(rank)
            except (TypeError, ValueError):
                continue

    rosters = storage.get_rosters(league.league_id)
    records = {r["roster_id"]: team_record(r) for r in rosters}
    roster_of_player, starts, points = {}, {}, {}
    player_positions: dict[str, str] = {}
    for r in rosters:
        for pid in (r.get("players") or []):
            p_ = storage.get_player(pid) or {}
            nm = p_.get("full_name")
            if nm:
                roster_of_player[nm] = r["roster_id"]
                player_positions[nm] = (p_.get("position") or "").upper()
    for wk in range(1, max(1, weeks_played) + 1):
        for row in storage.get_matchups(league.league_id, wk):
            pp = row.get("players_points") or {}
            for pid in (row.get("starters") or []):
                nm = (storage.get_player(pid) or {}).get("full_name")
                if nm:
                    starts[nm] = starts.get(nm, 0) + 1
            for pid, pts in pp.items():
                nm = (storage.get_player(pid) or {}).get("full_name")
                if nm:
                    points[nm] = round(points.get(nm, 0.0) + float(pts or 0), 1)

    standings = [{"roster_id": rid, "wins": rec["wins"], "losses": rec["losses"],
                  "pf": rec["fpts"]} for rid, rec in records.items()]
    board = model_board(profile=profile, names=public_names,
                        slugs={rid: "" for rid in public_names},
                        standings=standings, form=None,
                        weeks_played=weeks_played)
    model_rank = {r["roster_id"]: r["rank"] for r in board["rows"]}

    outlook = playoff_outlook(storage, league, week)
    playoff = {}
    for rid, t in (outlook.get("teams") or {}).items():
        playoff[rid] = (t["band"] if outlook.get("stage") == "bands"
                        else f"{t['odds']:.0%} ({t['band']})")

    moves: dict[int, list[dict]] = {}
    for row in analyze_transactions(storage, league, 18):
        for rid in row["rids"]:
            moves.setdefault(rid, []).append({
                "line": describe_move(row), "rank_shift": row.get("rank_shift"),
                "outcome": row.get("outcome"),
                "questionable": row["rationale"]["kind"] == "questionable"})

    matchup_results = _last_results(storage, league, weeks_played, public_names)
    return {
        "week": week, "weeks_played": weeks_played, "n_teams": profile["n"],
        "public_names": public_names, "positional_ranks": ranks,
        "opening_ranks": opening, "records": records,
        "all_play": all_play(scores), "model_rank": model_rank,
        "playoff": playoff, "moves": moves,
        "roster_of_player": roster_of_player, "starts": starts, "points": points,
        "player_positions": player_positions,
        "matchup_results": matchup_results,
    }


def _last_results(storage: Storage, league: League, weeks_played: int,
                  public_names: dict[int, str]) -> dict[int, dict]:
    """Each roster's most recent completed result, for matchup takes."""
    out: dict[int, dict] = {}
    for wk in range(1, weeks_played + 1):
        rows = [r for r in storage.get_matchups(league.league_id, wk)
                if r.get("matchup_id") is not None]
        by_mid: dict[int, list[dict]] = {}
        for r in rows:
            by_mid.setdefault(r["matchup_id"], []).append(r)
        for pair in by_mid.values():
            if len(pair) != 2:
                continue
            a, b = pair
            pa, pb = float(a.get("points") or 0), float(b.get("points") or 0)
            if not (pa or pb):
                continue
            for me, them, mine, theirs in ((a, b, pa, pb), (b, a, pb, pa)):
                out[me["roster_id"]] = {
                    "week": wk, "won": mine > theirs, "tie": mine == theirs,
                    "line": (f"{public_names.get(me['roster_id'])} "
                             f"{_fmt(mine)} – {_fmt(theirs)} "
                             f"{public_names.get(them['roster_id'])}")}
    return out


def evaluate_all(storage: Storage, league: League, season: str,
                 week: int) -> list[dict]:
    """Re-evaluate every unsettled take and persist what was computed.
    Called from sync; the public build only ever reads the stored result."""
    open_rows = storage.open_takes(league.slug, season)
    if not open_rows:
        return []
    ctx = evaluation_context(storage, league, season, week)
    out = []
    for take in open_rows:
        result = evaluate_take(take, ctx)
        storage.record_take_evaluation(
            take["take_id"], recommended_status=result["recommended_status"],
            evidence=result["evidence"])
        out.append({**take, **result})
    return out


# -------------------------------------------------- retroactive capture
#
# Seeding the system from issues already published, without rewriting them
# and without auto-creating anything. The scan is tuned for PRECISION: three
# excellent candidates the Commissioner actually tracks beat twenty he has
# to wade through, and a noisy list is a list nobody opens twice.

# A candidate has to sound like an assertion about the future, not a
# description of the past. These are the constructions that carry a bet.
_CLAIM_SIGNALS = [
    (r"\bthe assumption that\b|\bassumption[s]? (?:here|is|are)\b|\bassumed\b",
     3, "states an assumption"),
    (r"\b(?:can|will|could|might) (?:break|sink|carry|decide|cost|save)\b",
     3, "names a failure or success condition"),
    (r"\bif (?:it|he|they|this|\w+) (?:hits|works|holds|lands|is the future)\b",
     3, "conditional bet"),
    (r"\bcarr(?:y|ies|ying)\b|\bdecide[sd]?\b|\bhinges?\b|\brests? on\b",
     3, "makes the season depend on something"),
    (r"\b(?:bet|bets|gamble|conviction|committed)\b", 2, "frames a bet"),
    (r"\bhigh variance\b|\bpriced accordingly\b|\bflammable\b|\bpremium\b",
     2, "prices risk"),
    (r"\bthe (?:most|best|worst|strongest|weakest|boldest|steepest|single largest)\b",
     2, "superlative claim"),
    (r"\b(?:should|will|expect|projects? to|is going to|gonna)\b",
     2, "forward-looking"),
    (r"\b(?:sole|only) \w+ to\b|\bone more than\b", 2, "uniqueness claim"),
    (r"\brun into (?:issues|problems|trouble)\b|\bcry for help\b|\bstain\b",
     2, "predicts trouble"),
    (r"\b(?:concentrat\w+|stacked?|stacking|triple)\b", 1, "concentration risk"),
    (r"\bhedge[sd]?\b|\bcover\b", 1, "frames a hedge"),
    (r"\b(?:thin|problem|weakness|exposure|risk|risky)\b", 1, "names a vulnerability"),
    (r"\bdepth\b", 1, "depth claim"),
]
# Things that look like claims but are not testable, or are jokes about the
# author. Cheap to list, and each one removes a whole class of noise.
_CLAIM_BLOCKERS = [
    r"^\s*(?:full|the full) (?:board|draft board)",       # site signposting
    r"\bis on the (?:draft page|site|board)\b",
    r"\bgood luck\b|\bgodspeed\b|\bwelcome to\b",          # pleasantries
    r"\bcomplain about it\b|\bnuff said\b",
    r"\bnext (?:week|weekend)\b.*\bmatchup",               # scheduling notes
]
# A sentence whose subject is the whole league, not the team whose capsule it
# happens to sit under. Section-closing summaries land after the last team
# heading and would otherwise be attributed to that team.
_LEAGUE_WIDE_RE = re.compile(
    r"\bwe are a league\b|\bthe (?:whole|entire) (?:room|league)\b|"
    r"\bfor the whole league\b|\ball (?:twelve|ten|of us)\b|"
    r"\b(?:twelve|ten) of us\b|\bthis league\b|\bevery team\b|"
    r"\bone structural finding\b|\bacross the league\b", re.I)
# Special-teams "premiums" are artifacts of a board that ranks every kicker
# and defense below the draftable range — the calibration decision this repo
# already enforces on the Draft page and in the publication gate. Tracking
# one as a Take would re-import the error we just corrected in the 2026
# Surfeit issue, so a claim that is ONLY about special teams is not offered.
_SPECIAL_TEAMS_CLAIM_RE = re.compile(
    r"\bkicker|\bkickers\b|\bdefense[s]?\b|\bDST\b|\bD/ST\b", re.I)
MIN_CANDIDATE_WORDS = 9
MAX_CANDIDATE_WORDS = 60
CANDIDATE_FLOOR = 3          # a sentence needs this much signal to make the list
MAX_CANDIDATES = 8


_FIRST_PERSON_RE = re.compile(r"\b(?:I|I'll|I've|my|me)\b")


def _near_duplicate(a: str, b: str, threshold: float = 0.6) -> bool:
    """The Lowdown and the rankings often make the same claim twice. Offering
    both is offering the Commissioner the same decision twice."""
    ta = {w.strip(".,;:!?'\"()").lower() for w in a.split() if len(w) > 3}
    tb = {w.strip(".,;:!?'\"()").lower() for w in b.split() if len(w) > 3}
    if not ta or not tb:
        return False
    return len(ta & tb) / len(ta | tb) >= threshold


def candidate_takes(snapshot: dict, *, name_tokens: dict[int, set[str]],
                    public_names: dict[int, str], slugs: dict[int, str],
                    player_positions: dict[str, str],
                    existing_quotes: set[str] | None = None,
                    author_roster_id: int | None = None) -> list[dict]:
    """Sentences from one published issue worth offering as Takes.

    Returns at most MAX_CANDIDATES, strongest first, each carrying WHY it
    was picked so the Commissioner can judge the judgment. Nothing here
    writes to the database."""
    from leaguepage.pubqa import QAContext, _team_blocks
    from leaguepage.receipts import _sentences

    existing = existing_quotes or set()
    qctx = QAContext(league_slug="", season="", issue_key="",
                     n_teams=len(name_tokens))
    qctx.name_tokens = name_tokens
    qctx.public_names = {rid: "" for rid in name_tokens}

    out: list[dict] = []
    for section in snapshot.get("sections", []):
        text = section.get("content_md") or ""
        # A capsule under "### 4. Swanson" is about Swanson even though the
        # sentences never repeat the name. Without the heading context the
        # scan drops every per-team capsule in the issue, which is most of
        # the good material.
        blocks = [(re.sub(r"\s+", " ", body), rid)
                  for _h, body, rid in _team_blocks(text, qctx)]
        for sentence in _sentences(text):
            heading_rid = next((rid for body, rid in blocks if sentence in body),
                               None)
            words = sentence.split()
            if not (MIN_CANDIDATE_WORDS <= len(words) <= MAX_CANDIDATE_WORDS):
                continue
            if "|" in sentence or sentence in existing:
                continue
            if any(re.search(p, sentence, re.I) for p in _CLAIM_BLOCKERS):
                continue
            score, reasons = 0, []
            for pattern, weight, why in _CLAIM_SIGNALS:
                if re.search(pattern, sentence, re.I):
                    score += weight
                    reasons.append(why)
            if score < CANDIDATE_FLOOR:
                continue
            subject = infer_subject(sentence, name_tokens=name_tokens,
                                   public_names=public_names, slugs=slugs)
            league_wide = bool(_LEAGUE_WIDE_RE.search(sentence))
            if (not subject.get("subject_type") and heading_rid is not None
                    and not league_wide):
                subject = {"subject_type": "team", "subject_roster_id": heading_rid,
                           "subject": slugs.get(heading_rid),
                           "subject_name": public_names.get(heading_rid)}
            # "I punted TE until round 14" is a claim about his own roster.
            # The author writes about himself in the first person, and those
            # are the takes worth keeping.
            if (not subject.get("subject_type") and author_roster_id
                    and not league_wide and _FIRST_PERSON_RE.search(sentence)):
                subject = {"subject_type": "team",
                           "subject_roster_id": author_roster_id,
                           "subject": slugs.get(author_roster_id),
                           "subject_name": public_names.get(author_roster_id)}
            players = infer_players(sentence, player_positions)
            # A claim only about kickers and defenses is a claim about the
            # reference board's shape, not about a roster decision.
            st_only = (players and all(
                (player_positions.get(p) or "") in ("K", "DEF", "DST")
                for p in players))
            if st_only and _SPECIAL_TEAMS_CLAIM_RE.search(sentence):
                continue
            if not players and _SPECIAL_TEAMS_CLAIM_RE.search(sentence) \
                    and re.search(r"premium|reach|early|tax", sentence, re.I):
                continue
            # A claim nobody can be held to is not a Take. It has to be
            # about a team or a named player, or there is nothing to check.
            if not subject.get("subject_type") and not players:
                continue
            if subject.get("subject_type"):
                score += 2
                reasons.append(f"names {subject['subject_name']}")
            if players:
                score += 1
                reasons.append("names " + ", ".join(players[:2]))
            out.append({
                "quote": sentence,
                "score": score,
                "reasons": reasons,
                "topic": infer_topic(sentence),
                "section": section.get("module_key"),
                "section_title": section.get("title"),
                "issue_key": snapshot.get("issue_key"),
                "issue_label": snapshot.get("issue_label"),
                "href": snapshot.get("href"),
                "players": players,
                **subject,
            })
    out.sort(key=lambda c: (-c["score"], c["quote"]))
    deduped: list[dict] = []
    for c in out:
        if any(_near_duplicate(c["quote"], k["quote"]) for k in deduped):
            continue
        deduped.append(c)
    return deduped[:MAX_CANDIDATES]


# ----------------------------------------------------------- public view
#
# The only path from a Take to a reader. Three gates, all of which must
# pass: the Commissioner marked it public, the engine has something to say,
# and provenance survives. A take that fails any of them simply does not
# appear, which is why the front page can honestly show no receipt at all.

PUBLICABLE = (LEANING_RIGHT, LEANING_WRONG, RESOLVED_RIGHT, RESOLVED_WRONG)


def public_receipt(take: dict, *, names: dict[int, str]) -> dict | None:
    """One reader-facing receipt, or None.

    `verbatim` decides the framing. A quote the Commissioner edited when he
    tracked it is presented as a paraphrase — "he wrote, in substance" — and
    never inside quotation marks, because presenting a paraphrase as a
    quotation is the one thing an archive must never do."""
    status = take.get("status")
    # The engine's reading is only used when he has not ruled; his ruling wins.
    effective = status if status in PUBLICABLE else take.get("recommended_status")
    if not take.get("public") or effective not in PUBLICABLE:
        return None
    if not take.get("href") or not take.get("issue_key"):
        return None            # no provenance, no publication
    evidence = [e for e in (take.get("evidence") or []) if e]
    if not evidence:
        return None
    rid = take.get("subject_roster_id")
    return {
        "take_id": take["take_id"],
        "quote": take["quote"],
        "verbatim": bool(take.get("verbatim", True)),
        "attribution": ("wrote" if take.get("verbatim", True)
                        else "wrote, in substance"),
        "status": PUBLIC_STATUS[effective],
        "settled": effective in (RESOLVED_RIGHT, RESOLVED_WRONG),
        "evidence": evidence[:3],
        "issue_key": take["issue_key"],
        "issue_label": (take.get("issue_key") or "").replace(
            "week-", "Week ").replace("draft", "Draft Issue"),
        "href": take["href"],
        "subject_roster_id": rid,
        "subject_name": names.get(rid) if rid else take.get("subject_name"),
        "when": ("Week " + str(take["week"])) if take.get("week") else "Draft Issue",
    }


def public_receipts(storage: Storage, league: League, season: str,
                    names: dict[int, str]) -> list[dict]:
    """Every publishable receipt, strongest first. Settled verdicts and
    claims moving against their author are the interesting ones."""
    out = []
    for take in storage.public_takes(league.slug, season):
        r = public_receipt(take, names=names)
        if r:
            out.append(r)
    order = {"BUSTED": 0, "UNDER PRESSURE": 1, "AGING WELL": 2}
    out.sort(key=lambda r: (order.get(r["status"], 9), -r["take_id"]))
    return out
