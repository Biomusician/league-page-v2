"""Weekly matchup analysis — deterministic facts with provenance.

Pairs the week's Sleeper matchup rows into matchups and computes everything
the data actually supports: records, standings, points ranks, score history,
all-play, streaks, average margin, lineup efficiency (when player points
exist), H2H within the synced league, and recent transactions. Metrics the
data cannot support are set to None with the absence stated in `unavailable`
rather than fabricated — projections in particular: Sleeper's public API does
not expose them, so projection fields stay null until a projection source is
added.

Works for any league size; team identity resolution is shared with the draft
layer.
"""
from __future__ import annotations

from collections import defaultdict

from leaguepage import evidence
from leaguepage.config import League
from leaguepage.draft_analysis import _team_identity, slugify
from leaguepage.storage import Storage

FLEX_ELIGIBLE = {
    "FLEX": ("RB", "WR", "TE"),
    "SUPER_FLEX": ("QB", "RB", "WR", "TE"),
    "WRRB_FLEX": ("RB", "WR"),
    "REC_FLEX": ("WR", "TE"),
}


def team_record(roster: dict) -> dict:
    s = roster.get("settings") or {}
    fpts = float(s.get("fpts", 0)) + float(s.get("fpts_decimal", 0)) / 100.0
    return {"wins": s.get("wins", 0), "losses": s.get("losses", 0),
            "ties": s.get("ties", 0), "fpts": round(fpts, 2)}


def _standings(rosters: list[dict]) -> dict[int, int]:
    """roster_id -> standings position (1-based): wins desc, then fpts desc."""
    ranked = sorted(
        rosters,
        key=lambda r: (-(r.get("settings") or {}).get("wins", 0), -team_record(r)["fpts"]),
    )
    return {r["roster_id"]: i + 1 for i, r in enumerate(ranked)}


def weekly_scores(storage: Storage, league_id: str, through_week: int) -> dict[int, list[tuple[int, float]]]:
    """roster_id -> [(week, points)] for completed weeks (points > 0 for
    anyone that week, so an unplayed/preseason week contributes nothing)."""
    out: dict[int, list[tuple[int, float]]] = defaultdict(list)
    for week in range(1, through_week + 1):
        rows = storage.get_matchups(league_id, week)
        if not rows or not any((r.get("points") or 0) > 0 for r in rows):
            continue
        for r in rows:
            out[r["roster_id"]].append((week, float(r.get("points") or 0)))
    return out


def all_play(scores: dict[int, list[tuple[int, float]]]) -> dict[int, dict]:
    """For each roster: record if it had played every other roster every week."""
    by_week: dict[int, dict[int, float]] = defaultdict(dict)
    for rid, rows in scores.items():
        for week, pts in rows:
            by_week[week][rid] = pts
    result: dict[int, dict] = defaultdict(lambda: {"wins": 0, "losses": 0, "ties": 0})
    for week, pts_by_roster in by_week.items():
        for rid, pts in pts_by_roster.items():
            for other, opts in pts_by_roster.items():
                if other == rid:
                    continue
                if pts > opts:
                    result[rid]["wins"] += 1
                elif pts < opts:
                    result[rid]["losses"] += 1
                else:
                    result[rid]["ties"] += 1
    for rid, rec in result.items():
        games = rec["wins"] + rec["losses"] + rec["ties"]
        rec["pct"] = round(rec["wins"] / games, 3) if games else None
    return dict(result)


def optimal_points(roster_positions: list[str], players: list[dict]) -> float | None:
    """Best achievable starter total given slots and per-player points.
    players: [{position, points}]. Greedy: fixed slots take their best,
    then flex slots take the best remaining eligible. None if no data."""
    if not players:
        return None
    pool = sorted(players, key=lambda p: -p["points"])
    used = [False] * len(pool)
    total = 0.0
    slots = [s for s in roster_positions if s != "BN"]
    fixed = [s for s in slots if s not in FLEX_ELIGIBLE]
    flex = [s for s in slots if s in FLEX_ELIGIBLE]
    for slot in fixed:
        for i, p in enumerate(pool):
            if not used[i] and p["position"] == slot:
                used[i] = True
                total += p["points"]
                break
    for slot in flex:
        eligible = FLEX_ELIGIBLE[slot]
        for i, p in enumerate(pool):
            if not used[i] and p["position"] in eligible:
                used[i] = True
                total += p["points"]
                break
    return round(total, 2)


def lineup_efficiency(storage: Storage, roster_positions: list[str], row: dict) -> dict | None:
    """Actual starter points vs optimal lineup for one matchup row."""
    players_points = row.get("players_points") or {}
    if not players_points or not any(v > 0 for v in players_points.values()):
        return None
    players = []
    for pid, pts in players_points.items():
        p = storage.get_player(pid) or {}
        players.append({"position": p.get("position") or "?", "points": float(pts)})
    optimal = optimal_points(roster_positions, players)
    actual = float(row.get("points") or 0)
    if not optimal:
        return None
    return {"actual": actual, "optimal": optimal,
            "efficiency": round(actual / optimal, 3) if optimal else None}


def _streak(scores_and_results: list[tuple[int, bool]]) -> str | None:
    """[(week, won)] chronological -> e.g. 'W3' / 'L2'."""
    if not scores_and_results:
        return None
    last = scores_and_results[-1][1]
    n = 0
    for _, won in reversed(scores_and_results):
        if won == last:
            n += 1
        else:
            break
    return f"{'W' if last else 'L'}{n}"


def head_to_head(storage: Storage, league_id: str, a: int, b: int, through_week: int) -> dict:
    """H2H within the synced league's history (this league id only)."""
    meetings = []
    for week in range(1, through_week + 1):
        rows = storage.get_matchups(league_id, week)
        pair = {r["roster_id"]: r for r in rows
                if r.get("matchup_id") is not None and r["roster_id"] in (a, b)}
        if len(pair) == 2 and pair[a].get("matchup_id") == pair[b].get("matchup_id"):
            pa, pb = float(pair[a].get("points") or 0), float(pair[b].get("points") or 0)
            if pa > 0 or pb > 0:
                meetings.append({"week": week, "points": {a: pa, b: pb},
                                 "winner": a if pa > pb else (b if pb > pa else None)})
    wins_a = sum(1 for m in meetings if m["winner"] == a)
    wins_b = sum(1 for m in meetings if m["winner"] == b)
    return {"meetings": meetings, "record": {a: wins_a, b: wins_b},
            "last_meeting": meetings[-1] if meetings else None}


def recent_transactions(storage: Storage, league_id: str, roster_ids: set[int],
                        week: int, weeks_back: int = 2) -> list[dict]:
    out = []
    for w in range(max(1, week - weeks_back), week + 1):
        for t in storage.get_transactions(league_id, w):
            if t.get("status") != "complete":
                continue
            touched = set((t.get("adds") or {}).values()) | set((t.get("drops") or {}).values())
            if touched & roster_ids:
                out.append({
                    "week": w, "type": t.get("type"),
                    "adds": t.get("adds"), "drops": t.get("drops"),
                    "faab": sum(x.get("amount", 0) for x in (t.get("waiver_budget") or [])) or None,
                    "evidence": [f"sleeper:transaction:{t.get('transaction_id')}"],
                })
    return out


def analyze_week(
    storage: Storage,
    league: League,
    week: int,
    *,
    managers: dict[str, dict] | None = None,
) -> dict | None:
    """All matchups for a league-week with computed context. None if the week
    has no matchup rows at all."""
    league_data = storage.get_league(league.league_id) or {}
    season = str(league_data.get("season") or "")
    rows = storage.get_matchups(league.league_id, week)
    if not rows:
        return None
    rosters = storage.get_rosters(league.league_id)
    users = {u["user_id"]: u for u in storage.get_league_users(league.league_id)}
    roster_positions = league_data.get("roster_positions") or []
    standings = _standings(rosters)
    scores = weekly_scores(storage, league.league_id, week - 1)
    ap = all_play(scores)

    # points-for rank from roster settings
    fpts_rank = {
        r["roster_id"]: i + 1
        for i, r in enumerate(sorted(rosters, key=lambda r: -team_record(r)["fpts"]))
    }

    # Human-facing labels resolve through the public-name precedence
    # (commissioner override > Sleeper name > neutral Roster N); slugs stay
    # on the Sleeper-name-or-roster-N rule for stability (they land in git
    # paths and URLs and must not churn on display renames).
    from leaguepage.team_names import resolve_public_names

    try:
        public = resolve_public_names(storage, league)
    except Exception:
        public = {}

    teams: dict[int, dict] = {}
    used_slugs: set[str] = set()
    for r in rosters:
        identity = _team_identity(league, r, users, managers or {})
        # Privacy: same fallback rule as the draft layer — unnamed teams slug
        # to roster-N, never to a Sleeper handle (slugs land in git paths/URLs).
        base = slugify(identity["team_name"]) if identity["team_name"] else f"roster-{r['roster_id']}"
        slug = base if base not in used_slugs else f"{base}-{r['roster_id']}"
        used_slugs.add(slug)
        identity["team_slug"] = slug
        identity["display_name"] = ((public.get(r["roster_id"]) or {}).get("name")
                                    or identity["team_name"]
                                    or f"Roster {r['roster_id']}")
        rec = team_record(r)
        history = scores.get(r["roster_id"], [])
        results = []
        for wk, pts in history:
            # win/loss inferred from that week's pair
            pair = [x for x in storage.get_matchups(league.league_id, wk)
                    if x.get("matchup_id") is not None]
            mid = next((x.get("matchup_id") for x in pair if x["roster_id"] == r["roster_id"]), None)
            opp = next((x for x in pair if x.get("matchup_id") == mid and x["roster_id"] != r["roster_id"]), None)
            if opp is not None:
                results.append((wk, pts > float(opp.get("points") or 0)))
        margins = []
        for wk, pts in history:
            pair = [x for x in storage.get_matchups(league.league_id, wk)
                    if x.get("matchup_id") is not None]
            mid = next((x.get("matchup_id") for x in pair if x["roster_id"] == r["roster_id"]), None)
            opp = next((x for x in pair if x.get("matchup_id") == mid and x["roster_id"] != r["roster_id"]), None)
            if opp is not None:
                margins.append(pts - float(opp.get("points") or 0))
        identity.update({
            "record": rec,
            "standing": standings[r["roster_id"]],
            "points_rank": fpts_rank[r["roster_id"]],
            "recent_scores": [pts for _, pts in history[-3:]],
            "streak": _streak(results),
            "avg_margin": round(sum(margins) / len(margins), 2) if margins else None,
            "all_play": ap.get(r["roster_id"]),
        })
        teams[r["roster_id"]] = identity

    pairs: dict[int, list[dict]] = defaultdict(list)
    for row in rows:
        if row.get("matchup_id") is not None:
            pairs[row["matchup_id"]].append(row)

    matchups = []
    played = any((r.get("points") or 0) > 0 for r in rows)
    for mid, pair in sorted(pairs.items()):
        if len(pair) != 2:
            continue
        pair.sort(key=lambda r: r["roster_id"])
        a, b = pair
        ta, tb = teams[a["roster_id"]], teams[b["roster_id"]]
        slug = f"{ta['team_slug']}-vs-{tb['team_slug']}"
        h2h = head_to_head(storage, league.league_id, a["roster_id"], b["roster_id"], week - 1)
        unavailable = ["projected_score: no projection source (Sleeper's public API "
                       "does not expose projections); never fabricated"]
        if not played:
            unavailable.append("actual_points: games not yet played")
        if not scores:
            unavailable.append("all_play/streaks/margins: no completed weeks yet")
        matchups.append({
            "league": league.slug,
            "season": season,
            "week": week,
            "matchup_id": mid,
            "matchup_slug": slug,
            "teams": [
                {**t, "points": float(r.get("points") or 0) if played else None,
                 "starters": r.get("starters"),
                 "lineup_efficiency": lineup_efficiency(storage, roster_positions, r) if played else None}
                for t, r in ((ta, a), (tb, b))
            ],
            "projection": {"a": None, "b": None, "margin": None,
                           "source": None},
            "h2h": h2h,
            "recent_transactions": recent_transactions(
                storage, league.league_id, {a["roster_id"], b["roster_id"]}, week),
            "unavailable": unavailable,
            "evidence": [
                evidence.league_ref(league.league_id),
                f"sleeper:matchup:{league.league_id}:{week}:{mid}",
                evidence.roster_ref(league.league_id, a["roster_id"]),
                evidence.roster_ref(league.league_id, b["roster_id"]),
            ],
        })

    return {
        "league": league.slug,
        "league_name": league_data.get("name"),
        "season": season,
        "week": week,
        "total_teams": league_data.get("total_rosters") or len(rosters),
        # playoff shape comes from the league payload, never hardcoded
        "playoff_teams": int((league_data.get("settings") or {})
                             .get("playoff_teams") or 6),
        "playoff_week_start": int((league_data.get("settings") or {})
                                  .get("playoff_week_start") or 15),
        "weeks_played": len({wk for rows_ in scores.values() for wk, _ in rows_}),
        "matchups": matchups,
        "teams": {t["team_slug"]: t for t in teams.values()},
    }
