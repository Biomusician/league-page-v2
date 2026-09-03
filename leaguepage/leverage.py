"""What this week's games are actually worth, and who to root for.

Until the sync started fetching future weeks, the playoff model paired the
league at random for every remaining week. You cannot ask "how much does
Sunday's game move my odds" of a model that does not know who is playing
whom, so this module could not exist. Now it can.

Both numbers come out of one simulation run rather than several. Inside each
simulated season the model already knows who won this week's games and who
made the playoffs, so conditioning on those two facts is free:

    leverage(team)  = P(playoffs | they win this week)
                    - P(playoffs | they lose this week)

    rooting(team, other game)
                    = P(team makes playoffs | side A wins that game)
                    - P(team makes playoffs | side B wins that game)

Neither is a prediction. Both are statements about which results matter,
which is a different and more interesting thing: a manager who is 90% in and
90% out has nothing at stake on Sunday, and telling him so is more honest
than printing a percentage that will not move.
"""
from __future__ import annotations

import random
import statistics

from leaguepage.config import League
from leaguepage.matchup_analysis import team_record, weekly_scores
from leaguepage.storage import Storage
from leaguepage.team_analytics import FORM_WINDOW, remaining_schedule

# Below this a swing is noise dressed as a stake.
MATERIAL_SWING = 0.05
# A five-point swing off a two-percent base is not a stake, it is a
# formality. The exception is elimination, which is the most dramatic thing
# a regular-season game can carry and deserves saying out loud.
LIVE_FLOOR = 0.15
ELIMINATED = 0.02
# The table has to mean something before "what is at stake" does.
MIN_WEEKS = 4
MAX_ROOTING = 3


def _inputs(storage: Storage, league: League, through_week: int) -> dict | None:
    """Everything the simulation needs, or None if it is too early."""
    data = storage.get_league(league.league_id) or {}
    settings = data.get("settings") or {}
    scores = weekly_scores(storage, league.league_id, through_week)
    weeks_played = max((len(v) for v in scores.values()), default=0)
    if weeks_played < MIN_WEEKS:
        return None
    playoff_start = int(settings.get("playoff_week_start") or 15)
    this_week = weeks_played + 1
    if this_week >= playoff_start:
        return None
    schedule = remaining_schedule(storage, league.league_id, this_week,
                                  playoff_start - 1)
    if not schedule.get(this_week):
        return None
    means, sds = {}, {}
    for rid, rows in scores.items():
        vals = [s for _, s in rows]
        means[rid] = statistics.mean(vals)
        sds[rid] = max(8.0, statistics.pstdev(vals)) if len(vals) > 1 else 20.0
        recent = vals[-FORM_WINDOW:]
        means[rid] = 0.75 * means[rid] + 0.25 * (sum(recent) / len(recent))
    records = {r["roster_id"]: team_record(r)
               for r in storage.get_rosters(league.league_id)}
    return {"means": means, "sds": sds, "records": records,
            "schedule": schedule, "this_week": this_week,
            "playoff_start": playoff_start, "weeks_played": weeks_played,
            "spots": int(settings.get("playoff_teams") or 6)}


def _simulate(ctx: dict, league_id: str, sims: int) -> list[tuple[dict, set]]:
    """[(winners this week, rosters that made the playoffs)] per simulation.

    Seeded on the league and the week, so the same state always produces the
    same numbers: a leverage figure that wobbles between builds is not a
    figure anybody can quote.
    """
    means, sds = ctx["means"], ctx["sds"]
    rng = random.Random(f"leverage:{league_id}:{ctx['weeks_played']}")
    rids = sorted(means)
    remaining = list(range(ctx["this_week"], ctx["playoff_start"]))
    out = []
    for _ in range(sims):
        wins = {rid: ctx["records"][rid]["wins"] for rid in rids}
        pts = {rid: ctx["records"][rid]["fpts"] for rid in rids}
        winners: dict[int, int] = {}
        for wk in remaining:
            pairs = ctx["schedule"].get(wk)
            if not pairs:
                order = rng.sample(rids, len(rids))
                pairs = list(zip(order[::2], order[1::2]))
            for a, b in pairs:
                if a not in means or b not in means:
                    continue
                sa, sb = rng.gauss(means[a], sds[a]), rng.gauss(means[b], sds[b])
                pts[a] += sa
                pts[b] += sb
                winner = a if sa >= sb else b
                wins[winner] += 1
                if wk == ctx["this_week"]:
                    winners[a] = winner
                    winners[b] = winner
        table = sorted(rids, key=lambda rid: (-wins[rid], -pts[rid]))
        out.append((winners, set(table[:ctx["spots"]])))
    return out


def _conditional(runs, rid: int, subject: int, side: int) -> float | None:
    """P(subject makes the playoffs | `side` wins subject-or-other's game)."""
    hits = [made for winners, made in runs if winners.get(rid) == side]
    if len(hits) < 30:          # too thin a slice to quote
        return None
    return sum(1 for made in hits if subject in made) / len(hits)


def week_leverage(storage: Storage, league: League, through_week: int, *,
                  sims: int = 2000) -> dict | None:
    """Per team: how much this week's own result moves their playoff odds."""
    ctx = _inputs(storage, league, through_week)
    if not ctx:
        return None
    runs = _simulate(ctx, league.league_id, sims)
    pairs = ctx["schedule"][ctx["this_week"]]
    out: dict[int, dict] = {}
    for a, b in pairs:
        for me, them in ((a, b), (b, a)):
            win = _conditional(runs, me, me, me)
            lose = _conditional(runs, me, me, them)
            if win is None or lose is None:
                continue
            out[me] = {"opponent": them, "swing": round(win - lose, 3),
                       "if_win": round(win, 3), "if_lose": round(lose, 3),
                       "material": is_material(win, lose)}
    return {"week": ctx["this_week"], "teams": out,
            "spots": ctx["spots"], "sims": sims}


def rooting_interest(storage: Storage, league: League, through_week: int,
                     subject: int, *, sims: int = 2000) -> list[dict]:
    """The other games this team should care about, biggest swing first.

    Its own game is excluded: everybody knows to root for themselves.
    """
    ctx = _inputs(storage, league, through_week)
    if not ctx:
        return []
    runs = _simulate(ctx, league.league_id, sims)
    out = []
    for a, b in ctx["schedule"][ctx["this_week"]]:
        if subject in (a, b):
            continue
        pa = _conditional(runs, a, subject, a)
        pb = _conditional(runs, a, subject, b)
        if pa is None or pb is None:
            continue
        swing = pa - pb
        if abs(swing) < MATERIAL_SWING:
            continue
        out.append({"root_for": a if swing > 0 else b,
                    "against": b if swing > 0 else a,
                    "swing": round(abs(swing), 3)})
    out.sort(key=lambda r: -r["swing"])
    return out[:MAX_ROOTING]


def is_material(if_win: float, if_lose: float) -> bool:
    """Whether this result is worth telling a reader about."""
    if if_win - if_lose < MATERIAL_SWING:
        return False
    return if_win >= LIVE_FLOOR or if_lose < ELIMINATED


def describe_stake(if_win: float, if_lose: float) -> str:
    """Plain words for a pair of numbers most readers do not want in
    decimals, and honest about the two cases that are not really swings."""
    if if_lose < ELIMINATED:
        return "a loss ends it"
    if if_win >= 0.97 and if_lose < 0.80:
        # At 99 against 93 a win settles nothing; he was already in.
        return "a win settles it"
    pct = (if_win - if_lose) * 100
    if pct >= 25:
        return "decides most of it"
    if pct >= 15:
        return "swings it hard"
    return "matters"
