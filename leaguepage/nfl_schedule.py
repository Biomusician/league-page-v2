"""The NFL schedule, as reference data: who plays whom, and who is on bye.

For a long time the product could say nothing about byes at all, and the
matchup brief said so on every card rather than guess. The schedule is
public (nflverse's games.csv) and changes once a year, so it lives under
`refdata/nfl/` with its provenance beside it and is never fetched at
runtime. Team codes match the ones Sleeper puts on a player, so a starter's
NFL team joins the schedule directly.

What this can honestly say: a team absent from a week's regular-season rows
is on bye, and a team present has an opponent and a game day. What it
cannot say: whether a player will play, which stays with his injury status.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from leaguepage.config import REPO_ROOT

SCHEDULE_DIR = REPO_ROOT / "refdata" / "nfl"
NFL_TEAMS = 32


@lru_cache(maxsize=4)
def load_schedule(season: str | int, schedule_dir: Path | None = None) -> dict | None:
    """The season's schedule file, or None when there is no file for it."""
    path = (schedule_dir or SCHEDULE_DIR) / f"schedule_{season}.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    by_week: dict[int, list[dict]] = {}
    for r in data.get("rows") or []:
        if r.get("game_type", "REG") != "REG":
            continue
        by_week.setdefault(int(r["week"]), []).append(r)
    teams = sorted({r["home"] for rows in by_week.values() for r in rows}
                   | {r["away"] for rows in by_week.values() for r in rows})
    return {"season": str(data.get("season") or season),
            "fetched_at": data.get("fetched_at"), "source": data.get("source"),
            "teams": teams, "by_week": by_week}


def teams_on_bye(season: str | int, week: int, *, schedule_dir: Path | None = None) -> set[str] | None:
    """Teams with no game that week, or None when the schedule is unknown.

    None and an empty set are different answers: the first is "we cannot
    say", the second is "everybody plays".
    """
    sched = load_schedule(season, schedule_dir)
    if sched is None or week not in sched["by_week"]:
        return None
    playing = {r["home"] for r in sched["by_week"][week]} | {r["away"] for r in sched["by_week"][week]}
    return set(sched["teams"]) - playing


def game_for(season: str | int, week: int, team: str, *,
             schedule_dir: Path | None = None) -> dict | None:
    """{opponent, home, gameday} for a team's game that week, or None."""
    sched = load_schedule(season, schedule_dir)
    if sched is None:
        return None
    for r in sched["by_week"].get(week, []):
        if r["home"] == team:
            return {"opponent": r["away"], "home": True, "gameday": r.get("gameday")}
        if r["away"] == team:
            return {"opponent": r["home"], "home": False, "gameday": r.get("gameday")}
    return None


def opponent_label(game: dict | None) -> str:
    if not game:
        return "bye"
    return f"{'vs' if game['home'] else 'at'} {game['opponent']}"


def describe_source(season: str | int, *, schedule_dir: Path | None = None) -> str | None:
    sched = load_schedule(season, schedule_dir)
    if sched is None:
        return None
    when = (sched.get("fetched_at") or "")[:10]
    return f"nflverse schedule{' as of ' + when if when else ''}"
