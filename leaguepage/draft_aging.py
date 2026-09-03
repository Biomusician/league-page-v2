"""What happened to the picks, as a separate column from what they cost.

`draft_value.classify_pick` compares one selection against the reference
board on the day it was made. That comparison is immutable market analysis:
re-scoring it later with the benefit of results would be rewriting history
to win an argument, and a REACH that worked out was still a reach.

So this never touches REACH or STEAL. It answers a different question, and
answers it in the reader's own vocabulary: is the player still here, does he
start, and what has he scored. Two of those are answerable before a single
game is played, which is why this ships in the preseason rather than waiting
for week 3 -- "your biggest reach is already off your roster" is the funniest
sentence available in August.
"""
from __future__ import annotations

from leaguepage.config import League
from leaguepage.matchup_analysis import weekly_scores
from leaguepage.storage import Storage

# Below this many played weeks, only roster status is reported. A start count
# off one week says nothing about a draft pick.
MIN_WEEKS_FOR_USAGE = 2


def _pick_name(pick: dict) -> str:
    meta = pick.get("metadata") or {}
    return " ".join(x for x in (meta.get("first_name"), meta.get("last_name")) if x).strip()


def draft_aging(storage: Storage, league: League, *,
                through_week: int = 18) -> dict[int, list[dict]]:
    """roster_id -> [{name, position, pick_no, round, status, ...}].

    `status` is one of: held, traded (still in the league, someone else has
    him), gone (off every roster in the league).
    """
    drafts = storage.get_drafts_for_league(league.league_id)
    if not drafts:
        return {}
    picks = storage.get_draft_picks(drafts[0]["draft_id"])
    if not picks:
        return {}

    held_by: dict[str, int] = {}
    for r in storage.get_rosters(league.league_id):
        for pid in (r.get("players") or []):
            held_by[pid] = r["roster_id"]

    scores = weekly_scores(storage, league.league_id, through_week)
    weeks_played = max((len(v) for v in scores.values()), default=0)
    usage = _usage(storage, league, weeks_played) if \
        weeks_played >= MIN_WEEKS_FOR_USAGE else {}

    out: dict[int, list[dict]] = {}
    for p in picks:
        pid = p.get("player_id")
        drafted_by = p.get("roster_id")
        if pid is None or drafted_by is None:
            continue
        now = held_by.get(pid)
        if now is None:
            status = "gone"
        elif now == drafted_by:
            status = "held"
        else:
            status = "traded"
        row = {
            "player_id": pid,
            "name": _pick_name(p) or pid,
            "position": ((p.get("metadata") or {}).get("position") or "").upper(),
            "pick_no": p.get("pick_no"),
            "round": p.get("round"),
            "status": status,
            "now_roster_id": now,
        }
        row.update(usage.get(pid) or {})
        out.setdefault(drafted_by, []).append(row)
    for rows in out.values():
        rows.sort(key=lambda r: r["pick_no"] or 0)
    return out


def _usage(storage: Storage, league: League, weeks_played: int) -> dict[str, dict]:
    """Starts and points per player across the played weeks."""
    tally: dict[str, dict] = {}
    for wk in range(1, weeks_played + 1):
        for row in storage.get_matchups(league.league_id, wk):
            pts = row.get("players_points") or {}
            starters = set(row.get("starters") or [])
            for pid, v in pts.items():
                t = tally.setdefault(pid, {"starts": 0, "points": 0.0})
                t["points"] += float(v or 0)
                if pid in starters:
                    t["starts"] += 1
    return {pid: {"starts": t["starts"], "points": round(t["points"], 1),
                  "ppg": round(t["points"] / t["starts"], 1) if t["starts"] else None,
                  "weeks_played": weeks_played}
            for pid, t in tally.items()}


def team_summary(rows: list[dict]) -> dict:
    """Counts a manager can read in one glance."""
    return {
        "picks": len(rows),
        "held": sum(1 for r in rows if r["status"] == "held"),
        "traded": sum(1 for r in rows if r["status"] == "traded"),
        "gone": sum(1 for r in rows if r["status"] == "gone"),
    }


def departed_headliners(rows: list[dict], headline_picks: dict[str, str],
                        *, limit: int = 2) -> list[dict]:
    """Picks that were called out on draft night and are no longer here.

    `headline_picks` maps a player name to the label the draft page gave it
    (REACH / STEAL). The label is quoted, never recomputed: the point is
    that the market call and the roster decision have come apart.
    """
    out = []
    for r in rows:
        if r["status"] == "held":
            continue
        label = headline_picks.get(r["name"])
        if not label:
            continue
        out.append({**r, "label": label})
    out.sort(key=lambda r: r["pick_no"] or 0)
    return out[:limit]


def aging_line(row: dict) -> str:
    """One sentence about one pick, in a reader's vocabulary."""
    where = {"held": "still on the roster",
             "traded": "now on another roster",
             "gone": "no longer on any roster in the league"}[row["status"]]
    if row.get("starts") is None or not row.get("weeks_played"):
        return where.capitalize() + "."
    if row["starts"]:
        return (f"{where.capitalize()}, started {row['starts']} of "
                f"{row['weeks_played']} weeks for {row['points']:g} points.")
    return f"{where.capitalize()}, and has not started a week."
