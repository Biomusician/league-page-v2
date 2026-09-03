"""Build a whole synthetic season at an arbitrary state.

`populate_league` gives a league that has drafted and nothing else. Most of
the product only becomes interesting once games have been played, and every
season-state bug this project has shipped so far was a module that read fine
in one state and read like nonsense in another. So this builder takes the
state as a parameter: how many weeks have actually been played, where the
playoffs start, and what the week counter says.

Two details matter for realism:

* **Future weeks exist and are empty.** Sleeper publishes the pairings for
  the whole regular season up front and fills the points in as they are
  scored, so an unplayed week is rows with `matchup_id` set and `points` at
  zero. Code that treats "a row exists" as "a game happened" breaks here,
  which is the point.
* **Records are derived, never declared.** Wins, losses and points-for come
  from the scores this module wrote, so standings can never disagree with
  the box scores the way a hand-set record can.
"""
from __future__ import annotations

import random

from leaguepage.config import League

from fixtures import TEST_LEAGUE, league_payload, populate_league

# Enough of a roster to make starters/bench meaningful without pretending to
# model a real lineup: the site only ever asks for actual-vs-optimal.
STARTER_SLOTS = 7
BENCH_SLOTS = 4


def round_robin_pairs(teams: int, week: int) -> list[tuple[int, int]]:
    """A real rotating schedule (circle method), not a fixed pairing.

    `fixtures.default_pairs` pairs 1-2, 3-4 every week, which means a team
    can play the same opponent fourteen times and head-to-head history is
    meaningless. Here roster 1 is the pivot and the rest rotate, so over
    `teams - 1` weeks everybody plays everybody exactly once.
    """
    if teams < 2:
        return []
    order = list(range(1, teams + 1))
    if teams % 2:
        order.append(0)                      # bye marker
    n = len(order)
    fixed, rot = order[0], order[1:]
    shift = (week - 1) % (n - 1)
    rot = rot[shift:] + rot[:shift]
    line = [fixed] + rot
    pairs = []
    for i in range(n // 2):
        a, b = line[i], line[n - 1 - i]
        if a and b:                          # a 0 is the bye, skip the pair
            pairs.append((a, b))
    return pairs


def _team_players(rid: int) -> list[str]:
    base = (rid - 1) * (STARTER_SLOTS + BENCH_SLOTS)
    return [f"sp{base + i}" for i in range(STARTER_SLOTS + BENCH_SLOTS)]


def _week_scores(rng: random.Random, rid: int, base: float) -> tuple[float, dict, list]:
    """One team's week: a score, per-player points, and the started subset.

    The bench sometimes outscores a starter on purpose — lineup efficiency
    that is 100% every week for every team is not a measurement, it is a
    constant.
    """
    players = _team_players(rid)
    points = {}
    for i, pid in enumerate(players):
        mean = base / STARTER_SLOTS
        # bench players are worse on average but overlap with starters
        if i >= STARTER_SLOTS:
            mean *= 0.75
        points[pid] = round(max(0.0, rng.gauss(mean, mean * 0.45)), 2)
    starters = players[:STARTER_SLOTS]
    total = round(sum(points[p] for p in starters), 2)
    return total, points, starters


def populate_season(
    storage,
    league: League = TEST_LEAGUE,
    *,
    teams: int = 10,
    weeks_played: int = 0,
    current_week: int | None = None,
    playoff_week_start: int = 15,
    regular_weeks: int = 14,
    season: str = "2026",
    seed: int = 7,
    rounds: int = 3,
    picks: str = "complete",
) -> dict:
    """League, draft, full schedule, results for `weeks_played` weeks.

    Returns a summary dict: the schedule, the per-week scores actually
    written, and the derived records, so a test can assert against the same
    numbers the site will render.
    """
    draft = populate_league(storage, league, teams=teams, rounds=rounds,
                            picks=picks, season=season,
                            playoff_week_start=playoff_week_start)

    rosters = storage.get_rosters(league.league_id)
    for r in rosters:
        r["players"] = _team_players(r["roster_id"])
    storage.save_rosters(league.league_id, rosters)

    rng = random.Random(seed)
    # a stable per-team strength, so standings mean something across weeks
    strength = {rid: rng.uniform(88.0, 128.0) for rid in range(1, teams + 1)}

    schedule: dict[int, list[tuple[int, int]]] = {}
    scores: dict[int, dict[int, float]] = {}
    records = {rid: {"wins": 0, "losses": 0, "ties": 0, "pf": 0.0, "pa": 0.0}
               for rid in range(1, teams + 1)}

    for week in range(1, regular_weeks + 1):
        pairs = round_robin_pairs(teams, week)
        schedule[week] = pairs
        played = week <= weeks_played
        rows, wk_scores = [], {}
        for mid, (a, b) in enumerate(pairs, start=1):
            for rid in (a, b):
                if played:
                    pts, pp, st = _week_scores(rng, rid, strength[rid])
                else:
                    pts, pp, st = 0.0, {}, []
                wk_scores[rid] = pts
                rows.append({"roster_id": rid, "matchup_id": mid, "points": pts,
                             "starters": st, "players": _team_players(rid),
                             "players_points": pp})
            if played:
                pa, pb = wk_scores[a], wk_scores[b]
                records[a]["pf"] += pa; records[a]["pa"] += pb
                records[b]["pf"] += pb; records[b]["pa"] += pa
                if pa > pb:
                    records[a]["wins"] += 1; records[b]["losses"] += 1
                elif pb > pa:
                    records[b]["wins"] += 1; records[a]["losses"] += 1
                else:
                    records[a]["ties"] += 1; records[b]["ties"] += 1
        storage.save_matchups(league.league_id, week, rows)
        if played:
            scores[week] = wk_scores

    rosters = storage.get_rosters(league.league_id)
    for r in rosters:
        rec = records[r["roster_id"]]
        pf = round(rec["pf"], 2)
        r["settings"] = {
            "wins": rec["wins"], "losses": rec["losses"], "ties": rec["ties"],
            "fpts": int(pf), "fpts_decimal": int(round((pf % 1) * 100)),
            "fpts_against": int(rec["pa"]),
            "fpts_against_decimal": int(round((rec["pa"] % 1) * 100)),
        }
    storage.save_rosters(league.league_id, rosters)

    week = current_week if current_week is not None else max(1, weeks_played)
    storage.set_meta("current_week", str(week))
    return {"draft": draft, "schedule": schedule, "scores": scores,
            "records": records, "week": week, "weeks_played": weeks_played,
            "playoff_week_start": playoff_week_start}


# The matrix every season-state test walks. Names are the ones used in the
# product's own vocabulary so a failure reads as "midseason is broken".
SEASON_STATES = [
    # label,            weeks_played, current_week, playoff_week_start
    ("preseason",        0,  1,  15),
    ("week-1-pregame",   0,  1,  15),
    ("week-1-complete",  1,  1,  15),
    ("week-5",           5,  5,  15),
    ("week-10",         10, 10,  15),
    ("playoff-bubble",  13, 13,  15),
    ("playoffs",        14, 15,  15),
]
