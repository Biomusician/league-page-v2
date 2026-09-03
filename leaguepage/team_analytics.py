"""Deterministic team analytics: positional strength, form, outlook, playoffs.

The product rule this module serves: Sleeper already shows records, scores,
rosters and transactions; League-Page adds INTERPRETATION — what changed,
why it matters, what is trending, what to watch.

METHODOLOGY (public-facing summary lives in the site's Under the Hood):

Player value
  Preseason: the league's own reference consensus rank r becomes a linear
  points-proxy value = max(0, 250 - r). Only ordering and rough magnitude
  matter; an unranked player counts as replacement level (0).
  In-season (3+ played weeks): value = 0.5 * preseason value + 0.5 *
  (season PPG * 10), so observed production gradually outweighs the August
  consensus. The active stage is reported, never switched silently.

Positional strength (per league lineup settings, read dynamically from the
synced Sleeper league payload — a superflex league is never treated as 1QB
and a league without K/DST never gets K/DST rooms):
  Each team's optimal lineup is filled greedily (dedicated slots first,
  then FLEX/SUPER_FLEX by best remaining value). For each position:
    starter value  = value of lineup players at that position
    depth value    = top two bench values at that position
    room score     = 0.7 * starters + 0.3 * depth  (ranked across league)
    fragility      = share of the room held by its single best player
    surplus        = startable-quality players beyond lineup demand
  Labels come from rank quantiles (Strength / Above Average / Average /
  Weakness / Major Weakness) — small score gaps are deliberately not
  presented as meaningful differences.

Signal independence (anti-double-count): the playoff model uses ONLY
observed scoring (means/spread from played games); consensus ranks feed
positional strength only. Standings/form use actual results. The one
deliberate overlap: in-season positional values blend production, which
also drives form — they are presented as separate lenses, never chained
into one composite.
"""
from __future__ import annotations

import datetime as dt
import json
import random
import statistics

from leaguepage.adp import load_adp_for_league
from leaguepage.config import League
from leaguepage.draft_value import SKILL_POSITIONS
from leaguepage.matchup_analysis import (FLEX_ELIGIBLE, all_play, faab_cost,
                                         team_record, weekly_scores)
from leaguepage.storage import Storage

CORE_POSITIONS = ("QB", "RB", "WR", "TE", "K", "DEF")
IN_SEASON_MIN_WEEKS = 3
SIMS = 2000
FORM_WINDOW = 3
LABELS = ["Strength", "Above Average", "Average", "Weakness", "Major Weakness"]


# ------------------------------------------------------------- lineups

def lineup_slots(league_data: dict) -> list[str]:
    return [p for p in (league_data.get("roster_positions") or [])
            if p not in ("BN", "IR", "TAXI")]


def league_positions(league_data: dict) -> list[str]:
    """Positions this league actually starts, in display order."""
    slots = set(lineup_slots(league_data))
    out = [p for p in CORE_POSITIONS if p in slots]
    for flex, elig in FLEX_ELIGIBLE.items():
        if flex in slots:
            out += [p for p in elig if p not in out]
    return out


# ------------------------------------------------------- player values

def _season_ppg(storage: Storage, league_id: str, through_week: int) -> dict[str, float]:
    pts: dict[str, list[float]] = {}
    for wk in range(1, through_week + 1):
        for row in storage.get_matchups(league_id, wk):
            for pid, p in (row.get("players_points") or {}).items():
                if p is not None:
                    pts.setdefault(pid, []).append(float(p))
    return {pid: sum(v) / len(v) for pid, v in pts.items() if v}


def player_values(storage: Storage, league: League, *, adp=None,
                  weeks_played: int = 0,
                  rosters: list[dict] | None = None) -> tuple[dict[str, dict], str]:
    """player_id -> {name, position, value}; plus the methodology stage.
    `rosters` overrides stored rosters (reconstructed pre-transaction
    states); default is the live synced rosters."""
    adp = adp if adp is not None else load_adp_for_league(league)
    ppg = (_season_ppg(storage, league.league_id, weeks_played)
           if weeks_played >= IN_SEASON_MIN_WEEKS else {})
    stage = "in-season blend" if ppg else "preseason consensus ranks"
    out: dict[str, dict] = {}
    for r in (rosters if rosters is not None
              else storage.get_rosters(league.league_id)):
        for pid in (r.get("players") or []):
            if pid in out:
                continue
            p = storage.get_player(pid) or {}
            name = p.get("full_name") or f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()
            pos = (p.get("position") or "").upper()
            rank = adp.lookup(name, pos) if (adp and name) else None
            pre = max(0.0, 250.0 - rank) if rank is not None else 0.0
            value = 0.5 * pre + 0.5 * (ppg.get(pid, 0.0) * 10) if ppg else pre
            out[pid] = {"name": name or pid, "position": pos, "value": value}
    return out, stage


# --------------------------------------------------- positional engine

def _fill_lineup(slots: list[str], pool: dict[str, list[dict]]) -> dict[str, list[dict]]:
    """Greedy optimal lineup. pool: position -> players sorted by value desc
    (consumed). Returns position -> players used in the lineup."""
    used: dict[str, list[dict]] = {}
    dedicated = [s for s in slots if s not in FLEX_ELIGIBLE]
    flexes = [s for s in slots if s in FLEX_ELIGIBLE]
    for s in dedicated:
        if pool.get(s):
            used.setdefault(s, []).append(pool[s].pop(0))
    for s in flexes:
        best_pos, best_val = None, -1.0
        for pos in FLEX_ELIGIBLE[s]:
            if pool.get(pos) and pool[pos][0]["value"] > best_val:
                best_pos, best_val = pos, pool[pos][0]["value"]
        if best_pos:
            used.setdefault(best_pos, []).append(pool[best_pos].pop(0))
    return used


def positional_profile(storage: Storage, league: League, *, adp=None,
                       weeks_played: int = 0,
                       rosters: list[dict] | None = None) -> dict:
    """{'stage', 'positions', 'teams': {rid: {pos: {...}}}, 'ranks': {pos:
    {rid: rank}}, 'starter_ranks', 'depth_ranks'}. `rosters` overrides the
    stored rosters (reconstructed pre-transaction states)."""
    data = storage.get_league(league.league_id) or {}
    slots = lineup_slots(data)
    positions = league_positions(data)
    if rosters is None:
        rosters = storage.get_rosters(league.league_id)
    values, stage = player_values(storage, league, adp=adp,
                                  weeks_played=weeks_played, rosters=rosters)

    teams: dict[int, dict] = {}
    for r in rosters:
        rid = r["roster_id"]
        by_pos: dict[str, list[dict]] = {}
        for pid in (r.get("players") or []):
            pv = values.get(pid)
            if pv and pv["position"] in positions:
                by_pos.setdefault(pv["position"], []).append(pv)
        for lst in by_pos.values():
            lst.sort(key=lambda p: -p["value"])
        pool = {pos: list(lst) for pos, lst in by_pos.items()}
        used = _fill_lineup(slots, pool)
        team: dict[str, dict] = {}
        for pos in positions:
            starters = used.get(pos, [])
            bench = pool.get(pos, [])          # whatever the lineup didn't take
            sv = sum(p["value"] for p in starters)
            dv = sum(p["value"] for p in bench[:2])
            total = sum(p["value"] for p in (by_pos.get(pos) or []))
            top = (by_pos.get(pos) or [{}])[0].get("value", 0.0)
            team[pos] = {
                "starter_value": round(sv, 1), "depth_value": round(dv, 1),
                "room_score": round(0.7 * sv + 0.3 * dv, 1),
                "starters_used": len(starters),
                "top_player": (by_pos.get(pos) or [{}])[0].get("name"),
                "fragility": round(top / total, 2) if total > 0 else 0.0,
                "count": len(by_pos.get(pos) or []),
            }
        teams[rid] = team

    ranks: dict[str, dict[int, int]] = {}
    starter_ranks: dict[str, dict[int, int]] = {}
    depth_ranks: dict[str, dict[int, int]] = {}
    for pos in positions:
        for target, keyname in ((ranks, "room_score"), (starter_ranks, "starter_value"),
                                (depth_ranks, "depth_value")):
            order = sorted(teams, key=lambda rid: -teams[rid][pos][keyname])
            target[pos] = {rid: i + 1 for i, rid in enumerate(order)}
    # surplus: startable-quality players beyond what the lineup uses
    for pos in positions:
        med = statistics.median([teams[rid][pos]["starter_value"] /
                                 max(1, teams[rid][pos]["starters_used"] or 1)
                                 for rid in teams]) if teams else 0.0
        floor = med * 0.6
        for rid in teams:
            t = teams[rid][pos]
            # count from room_score inputs: approximate via count of players
            t["surplus"] = max(0, t["count"] - t["starters_used"] - (0 if t["depth_value"] > floor else 1))
    return {"stage": stage, "positions": positions, "teams": teams, "ranks": ranks,
            "starter_ranks": starter_ranks, "depth_ranks": depth_ranks,
            "n": len(teams)}


def label_for_rank(rank: int, n: int) -> str:
    q = rank / max(1, n)
    if q <= 0.2:
        return LABELS[0]
    if q <= 0.4:
        return LABELS[1]
    if q <= 0.7:
        return LABELS[2]
    if q <= 0.9:
        return LABELS[3]
    return LABELS[4]


def strengths_weaknesses(profile: dict, rid: int, *, max_strengths: int = 3,
                         max_weaknesses: int = 3) -> dict:
    """Concise factual strengths/weaknesses with starter-vs-depth nuance."""
    n = profile["n"]
    rows = []
    for pos in profile["positions"]:
        rows.append((pos, profile["ranks"][pos][rid],
                     profile["starter_ranks"][pos][rid],
                     profile["depth_ranks"][pos][rid],
                     profile["teams"][rid][pos]))
    rows.sort(key=lambda x: x[1])
    strengths, weaknesses = [], []
    for pos, rank, srank, drank, t in rows:
        # A room with nobody in it ties every other empty room at zero, and
        # the stable sort hands the lowest roster_id the top spot. An
        # abandoned team was publishing "TE room ranks 2/4 with real depth
        # behind the starters" about a roster holding no players at all.
        if not t.get("count"):
            continue
        if rank <= max(2, round(0.3 * n)) and len(strengths) < max_strengths:
            note = f"{pos} room ranks {rank}/{n}"
            if t["fragility"] >= 0.6 and t["count"] > 1:
                note += (f", but {int(t['fragility'] * 100)}% of that strength is "
                         f"{t['top_player']} (top-heavy)")
            elif drank <= max(2, round(0.3 * n)):
                note += " with real depth behind the starters"
            strengths.append({"position": pos, "rank": rank, "note": note})
    for pos, rank, srank, drank, t in reversed(rows):
        if rank >= round(0.7 * n) and len(weaknesses) < max_weaknesses:
            if srank <= round(0.5 * n) < drank:
                kind = "depth"
                note = (f"{pos} starters hold up ({srank}/{n}) but there is "
                        f"nothing behind them (depth {drank}/{n})")
            else:
                kind = "starters"
                note = f"{pos} room ranks {rank}/{n}"
            weaknesses.append({"position": pos, "rank": rank, "kind": kind, "note": note})
    return {"strengths": strengths, "weaknesses": weaknesses}


# ----------------------------------------------------- form and streaks

def recent_form(storage: Storage, league: League, through_week: int,
                window: int = FORM_WINDOW) -> dict[int, dict] | None:
    scores = weekly_scores(storage, league.league_id, through_week)
    if not scores or max(len(v) for v in scores.values()) < 2:
        return None
    w = min(window, max(len(v) for v in scores.values()))
    # Keep the week numbers. Collapsing the window to a single pseudo-week
    # made all_play compare one team's week 1 against another's week 3 and
    # multiplied the game count by the window size, which is not all-play at
    # all: it is a cross-product of scores.
    windowed = {rid: rows[-w:] for rid, rows in scores.items() if rows}
    avg = {rid: sum(pts for _, pts in rows) / len(rows)
           for rid, rows in windowed.items()}
    order = sorted(avg, key=lambda rid: -avg[rid])
    ap = all_play(windowed)
    # window is the integer week count; window_label is what a reader sees,
    # because "#2 scoring over the last 3" is missing its unit. The label is
    # per team, not per league: a team with a bye inside the window has
    # fewer weeks in it than the league maximum, and labelling its average
    # "the last 3 weeks" when it played two of them is a wrong denominator
    # printed as a fact.
    def _label(n: int) -> str:
        return f"{n} week{'' if n == 1 else 's'}"

    return {rid: {"window": len(rows), "window_label": _label(len(rows)),
                  "league_window": w, "avg": round(avg[rid], 1),
                  "rank": order.index(rid) + 1,
                  "all_play": ap.get(rid)} for rid, rows in windowed.items()}


def scoring_streaks(storage: Storage, league: League, through_week: int) -> dict[int, dict]:
    """Meaningful streaks only: 3+ result streaks, 3+ top/bottom-half weeks."""
    scores = weekly_scores(storage, league.league_id, through_week)
    out: dict[int, dict] = {}
    weeks = sorted({wk for rows in scores.values() for wk, _ in rows})
    halves: dict[int, list[bool]] = {rid: [] for rid in scores}
    for wk in weeks:
        wk_scores = {rid: s for rid, rows in scores.items() for w2, s in rows if w2 == wk}
        if not wk_scores:
            continue
        med = statistics.median(wk_scores.values())
        for rid, s in wk_scores.items():
            halves[rid].append(s >= med)
    for rid, flags in halves.items():
        streak = 0
        for f in reversed(flags):
            if f == flags[-1]:
                streak += 1
            else:
                break
        if flags and streak >= 3:
            out[rid] = {"kind": "top-half scoring" if flags[-1] else "bottom-half scoring",
                        "length": streak}
    return out


# --------------------------------------------------------- playoff model

def remaining_schedule(storage: Storage, league_id: str, first_week: int,
                       last_week: int) -> dict[int, list[tuple[int, int]]]:
    """The pairings for weeks that have not been played yet.

    Sleeper publishes the whole regular season up front and fills the points
    in as they are scored, so a future week comes back as rows with a real
    `matchup_id` and zero points. Simulating against those instead of
    against a random re-pairing of the league is the difference between a
    forecast and a disclaimer: strength of remaining schedule becomes a real
    quantity, and one game can be shown to matter more than another.

    A week is only offered here if nobody has scored in it. A week already
    played is history, not schedule.
    """
    out: dict[int, list[tuple[int, int]]] = {}
    for week in range(max(1, first_week), last_week + 1):
        rows = storage.get_matchups(league_id, week)
        if not rows or any((r.get("points") or 0) > 0 for r in rows):
            continue
        by_matchup: dict[object, list[int]] = {}
        for r in rows:
            mid = r.get("matchup_id")
            if mid is None:          # a bye, or a week Sleeper has not paired
                continue
            by_matchup.setdefault(mid, []).append(r["roster_id"])
        pairs = [(g[0], g[1]) for g in by_matchup.values() if len(g) == 2]
        if pairs:
            out[week] = pairs
    return out


def playoff_outlook(storage: Storage, league: League, through_week: int,
                    sims: int = SIMS) -> dict:
    """Transparent Monte Carlo on OBSERVED scoring only (no consensus ranks:
    see the module docstring's independence rule). Early season is honestly
    'too early'; midseason shows bands; late season shows percentages."""
    data = storage.get_league(league.league_id) or {}
    settings = data.get("settings") or {}
    spots = int(settings.get("playoff_teams") or 6)
    playoff_start = int(settings.get("playoff_week_start") or 15)
    scores = weekly_scores(storage, league.league_id, through_week)
    weeks_played = max((len(v) for v in scores.values()), default=0)
    base = {"playoff_teams": spots, "playoff_week_start": playoff_start,
            "weeks_played": weeks_played}
    if weeks_played < IN_SEASON_MIN_WEEKS:
        return {**base, "stage": "too_early",
                "note": f"Playoff outlook opens after {IN_SEASON_MIN_WEEKS} played weeks."}

    rosters = storage.get_rosters(league.league_id)
    records = {r["roster_id"]: team_record(r) for r in rosters}
    means, sds = {}, {}
    for rid, rows in scores.items():
        vals = [s for _, s in rows]
        means[rid] = statistics.mean(vals)
        sds[rid] = max(8.0, statistics.pstdev(vals)) if len(vals) > 1 else 20.0
        recent = vals[-FORM_WINDOW:]
        means[rid] = 0.75 * means[rid] + 0.25 * (sum(recent) / len(recent))
    # The number of weeks a team has played is not the number of the last
    # week it played. One unpaired or unsynced week in the middle makes them
    # differ, and the count then puts an already-played week inside
    # "remaining" — where it gets re-simulated against random opponents, so
    # the model replays history against teams nobody faced.
    latest_played = max((wk for rows in scores.values() for wk, _ in rows),
                        default=weeks_played)
    first_remaining = max(weeks_played, latest_played) + 1
    remaining = list(range(first_remaining, playoff_start))
    schedule = remaining_schedule(storage, league.league_id,
                                  first_remaining, playoff_start - 1)
    known = [wk for wk in remaining if schedule.get(wk)]
    rng = random.Random(f"{league.league_id}:{weeks_played}")
    rids = sorted(means)
    made = {rid: 0 for rid in rids}
    seeds: dict[int, list[int]] = {rid: [] for rid in rids}
    for _ in range(sims):
        wins = {rid: records[rid]["wins"] for rid in rids}
        # team_record returns "fpts". Reading "points_for" meant every
        # simulated season started every team at zero points, so the
        # tiebreak that decides the last playoff spot was throwing away all
        # the scoring that had actually happened.
        pts = {rid: records[rid]["fpts"] for rid in rids}
        for wk in remaining:
            pairs = schedule.get(wk)
            if not pairs:                            # unpaired week: fall back
                order = rng.sample(rids, len(rids))
                pairs = list(zip(order[::2], order[1::2]))
            for a, b in pairs:
                if a not in means or b not in means:
                    continue
                sa = rng.gauss(means[a], sds[a])
                sb = rng.gauss(means[b], sds[b])
                pts[a] += sa
                pts[b] += sb
                wins[a if sa >= sb else b] += 1
        table = sorted(rids, key=lambda rid: (-wins[rid], -pts[rid]))
        for seed, rid in enumerate(table[:spots], 1):
            made[rid] += 1
            seeds[rid].append(seed)
    odds = {rid: made[rid] / sims for rid in rids}
    stage = "bands" if weeks_played < 8 else "percentages"

    def band(p: float) -> str:
        if p >= 0.75:
            return "Strong Position"
        if p >= 0.45:
            return "In the Mix"
        if p >= 0.15:
            return "Work to Do"
        return "Danger"

    return {**base, "stage": stage,
            # Rounded to whole percentage points. The third decimal was
            # 0.1pp of precision on a figure whose Monte Carlo error alone is
            # a couple of points, before the far larger uncertainty in the
            # per-team means these draws come from.
            "teams": {rid: {"odds": round(odds[rid], 2), "band": band(odds[rid]),
                            # Median seed AMONG THE SIMS THEY QUALIFIED IN.
                            # A 6%-to-make-it team reporting "median seed 6"
                            # reads as a projection and is not one.
                            "median_seed_when_in": (statistics.median(seeds[rid])
                                                    if seeds[rid] else None)}
                      for rid in rids},
            "sims": sims,
            "schedule_weeks": len(known), "remaining_weeks": len(remaining),
            "note": schedule_note(len(known), len(remaining))}


def format_odds(p: float) -> str:
    """A probability from a finite simulation, written without claiming
    certainty.

    Zero of 2000 draws is not zero and 2000 of 2000 is not one, so "0%" and
    "100%" turn a sample into a guarantee. Rounding is the other half of it:
    0.995 is not certainty either, and "{:.0%}" prints it as 100%. Gate on
    what the reader would see rather than on the raw value.
    """
    text = f"{p:.0%}"
    if text == "0%":
        return "<1%"
    if text == "100%":
        return ">99%"
    return text


def schedule_note(known: int, total: int) -> str:
    """What the model actually simulated against, said plainly."""
    if not total:
        return "The regular season is complete."
    if known >= total:
        return ("Simulated against the actual remaining schedule, opponent by "
                "opponent.")
    if known:
        return (f"{known} of the {total} remaining weeks are simulated against "
                "the actual schedule; the rest as random league pairings.")
    return ("Remaining opponents are not synced, so they are simulated as "
            "random league pairings.")


# ------------------------------------------------- snapshots and deltas

def record_snapshot(storage: Storage, league: League, season: str, week: int,
                    *, adp=None) -> dict:
    """Persist this week's analytical state so later deltas are historical
    fact, not retroactive recomputation. Week 0 = preseason. Idempotent
    per (league, season, week): re-running a sync refreshes that week."""
    scores = weekly_scores(storage, league.league_id, max(week, 0))
    weeks_played = max((len(v) for v in scores.values()), default=0)
    profile = positional_profile(storage, league, adp=adp, weeks_played=weeks_played)
    rosters = storage.get_rosters(league.league_id)
    wins = {r["roster_id"]: team_record(r) for r in rosters}
    standing_order = sorted(wins, key=lambda rid: (-wins[rid]["wins"],
                                                   -wins[rid]["fpts"]))
    outlook = playoff_outlook(storage, league, max(week, 0))
    payload = {
        "week": week, "stage": profile["stage"],
        # The outlook's own stage, so a later delta can refuse to print a
        # percentage move that the site itself declines to show as a number.
        "playoff_stage": outlook.get("stage"),
        "positional_ranks": {pos: profile["ranks"][pos] for pos in profile["positions"]},
        # Before a game is played every team is 0-0 on 0 points, so this
        # sort is roster_id order wearing a rank. Storing it would make the
        # first real week diff against an arbitrary baseline and announce
        # movement nobody made.
        "standings": ({rid: i + 1 for i, rid in enumerate(standing_order)}
                      if weeks_played else None),
        "playoff": ({rid: t["odds"] for rid, t in outlook["teams"].items()}
                    if "teams" in outlook else None),
    }
    storage.set_meta(f"analytics_snapshot:{league.slug}:{season}:{week}",
                     json.dumps(payload))
    return payload


def get_snapshot(storage: Storage, league: League, season: str, week: int) -> dict | None:
    raw = storage.get_meta(f"analytics_snapshot:{league.slug}:{season}:{week}")
    return json.loads(raw) if raw else None


def snapshot_deltas(storage: Storage, league: League, season: str,
                    current_week: int) -> dict[int, list[str]]:
    """Human-readable per-team changes vs the most recent EARLIER snapshot."""
    current = get_snapshot(storage, league, season, current_week)
    prior = None
    for wk in range(current_week - 1, -1, -1):
        prior = get_snapshot(storage, league, season, wk)
        if prior:
            break
    if not current or not prior:
        return {}
    # Player valuation switches from reference ranks to a blend with scoring
    # at IN_SEASON_MIN_WEEKS. Across that boundary every room in the league
    # moves, on rosters nobody touched, and the site was publishing the
    # switch as "RB room #2 -> #8". Positional deltas only mean something
    # when both snapshots were measured the same way.
    same_stage = current.get("stage") == prior.get("stage")
    # Likewise the odds: the standings page refuses to print a percentage
    # while the outlook is in bands, so a delta must not print one either.
    # An older snapshot with no recorded stage is treated as not comparable.
    percentages = (current.get("playoff_stage") == "percentages"
                   and prior.get("playoff_stage") == "percentages")
    out: dict[int, list[str]] = {}

    def _i(d, rid):
        return d.get(str(rid), d.get(rid))

    cur_standings = current.get("standings") or {}
    prior_standings = prior.get("standings") or {}
    ranks = current["positional_ranks"] or {}
    subjects = cur_standings or next(iter(ranks.values()), {})
    for rid_key in subjects:
        rid = int(rid_key)
        notes = []
        s_now, s_then = _i(cur_standings, rid), _i(prior_standings, rid)
        if s_now and s_then and abs(s_now - s_then) >= 2:
            arrow = "↑" if s_now < s_then else "↓"
            notes.append(f"standings {s_then} → {s_now} {arrow}")
        if same_stage:
            for pos, ranks in current["positional_ranks"].items():
                r_now = _i(ranks, rid)
                r_then = _i((prior["positional_ranks"] or {}).get(pos, {}), rid)
                if r_now and r_then and abs(r_now - r_then) >= 3:
                    notes.append(f"{pos} room #{r_then} → #{r_now}")
        if percentages and current.get("playoff") and prior.get("playoff"):
            p_now, p_then = _i(current["playoff"], rid), _i(prior["playoff"], rid)
            if p_now is not None and p_then is not None and abs(p_now - p_then) >= 0.10:
                notes.append(f"playoff odds {format_odds(p_then)} → "
                             f"{format_odds(p_now)}")
        if notes:
            out[rid] = notes
    return out


# ------------------------------------------------------------- key moves

def key_moves(storage: Storage, league: League, through_week: int,
              values: dict[str, dict] | None = None) -> dict[int, list[dict]]:
    """Consequential transactions only: trades, meaningful FAAB, or an add
    whose player carries real value. No delta claims without snapshots."""
    if values is None:
        values, _ = player_values(storage, league)
    budget = float((storage.get_league(league.league_id) or {})
                   .get("settings", {}).get("waiver_budget") or 100)
    out: dict[int, list[dict]] = {}
    for wk in range(1, through_week + 1):
        for tx in storage.get_transactions(league.league_id, wk):
            if tx.get("status") != "complete":
                continue
            adds = tx.get("adds") or {}
            bids = faab_cost(tx)
            for pid, rid in adds.items():
                pv = values.get(pid) or {}
                consequential = (tx.get("type") == "trade"
                                 or bids >= 0.2 * budget
                                 or pv.get("value", 0) >= 100)
                if not consequential:
                    continue
                desc = (f"{'Trade' if tx.get('type') == 'trade' else 'Pickup'}: "
                        f"{pv.get('name', pid)}"
                        + (f" ({pv.get('position')})" if pv.get("position") else "")
                        + (f" for {bids} FAAB" if bids else ""))
                out.setdefault(rid, []).append({"week": wk, "type": tx.get("type"),
                                                "note": desc})
    return {rid: moves[-3:] for rid, moves in out.items()}


# ---------------------------------------------------------- team outlook

def team_outlook(storage: Storage, league: League, season: str, rid: int,
                 through_week: int, *, profile: dict | None = None) -> list[str]:
    """The 2-5 things currently defining this team. Preseason: construction
    facts. In-season: standing, form, streaks, deltas, moves."""
    profile = profile or positional_profile(storage, league,
                                            weeks_played=through_week)
    sw = strengths_weaknesses(profile, rid)
    signals: list[str] = []
    scores = weekly_scores(storage, league.league_id, through_week)
    weeks_played = len(scores.get(rid, []))
    if weeks_played:
        r = team_record(next(x for x in storage.get_rosters(league.league_id)
                             if x["roster_id"] == rid))
        signals.append(f"{r['wins']}-{r['losses']}"
                       + (f"-{r['ties']}" if r.get("ties") else ""))
        form = recent_form(storage, league, through_week)
        if form and rid in form:
            f = form[rid]
            signals.append(f"#{f['rank']} scoring over the last {f['window_label']}")
        st = scoring_streaks(storage, league, through_week).get(rid)
        if st:
            signals.append(f"{st['length']} straight weeks of {st['kind']}")
        for d in snapshot_deltas(storage, league, season, through_week).get(rid, [])[:2]:
            signals.append(d)
        for m in key_moves(storage, league, through_week).get(rid, [])[:1]:
            signals.append(m["note"])
    # The same calibration rule the briefing follows. A K or DEF line
    # survives only as the league's outright best or worst and never in
    # place of a skill-position line -- this is what renders as "defining
    # this team right now", and it was printing "K room ranks 2/10" two
    # lines under a paragraph promising the opposite.
    for group in (sw["strengths"], sw["weaknesses"]):
        pick = next((e for e in group
                     if (e.get("position") or "") in SKILL_POSITIONS), None)
        if pick is None and group and group[0].get("rank") in (1, profile["n"]):
            pick = group[0]
        if pick:
            signals.append(pick["note"])
    return signals[:5]


# ------------------------------------------- editorial integration layer

def league_shift_lines(storage: Storage, league: League, season: str,
                       through_week: int, names: dict[int, str]) -> list[str]:
    """The league's biggest current shifts, as ready-to-use brief lines.
    Empty preseason by design: no games, no shifts."""
    lines: list[str] = []
    deltas = snapshot_deltas(storage, league, season, through_week)
    for rid, notes in deltas.items():
        for n in notes[:1]:
            lines.append(f"• {names.get(rid, f'Roster {rid}')}: {n}")
    for rid, st in scoring_streaks(storage, league, through_week).items():
        lines.append(f"• {names.get(rid, f'Roster {rid}')}: {st['length']} straight "
                     f"weeks of {st['kind']}")
    form = recent_form(storage, league, through_week)
    if form:
        best = min(form, key=lambda rid: form[rid]["rank"])
        lines.append(f"• hottest: {names.get(best, best)} "
                     f"(#1 scoring over the last {form[best]['window_label']})")
    return lines[:6]


def roster_contrast_lines(profile: dict, rid_a: int, rid_b: int,
                          name_a: str, name_b: str) -> list[str]:
    """Positional contrast for a matchup brief. Fantasy rosters do not defend
    each other, so this is framed as construction contrast, never 'X covers Y'."""
    lines = []
    diffs = []
    # Skill rooms carry the comparison. Every manager is forced to draft a
    # kicker and a defense, so a K or DEF gap measures the reference board's
    # shape rather than anything either of these two decided to do.
    positions = [p for p in profile["positions"] if p in SKILL_POSITIONS] \
        or list(profile["positions"])
    for pos in positions:
        ra, rb = profile["ranks"][pos][rid_a], profile["ranks"][pos][rid_b]
        diffs.append((abs(ra - rb), pos, ra, rb))
    diffs.sort(reverse=True)
    for gap, pos, ra, rb in diffs[:3]:
        if gap >= 3:
            lines.append(f"• {pos} rooms: {name_a} #{ra} vs {name_b} #{rb}")
    for rid, nm in ((rid_a, name_a), (rid_b, name_b)):
        best = min(positions, key=lambda p: profile["ranks"][p][rid])
        lines.append(f"• {nm}'s best room: {best} "
                     f"(#{profile['ranks'][best][rid]}/{profile['n']})")
    return lines


def analytics_story_candidates(storage: Storage, league: League, season: str,
                               week: int, names: dict[int, str]) -> list[dict]:
    """Story Board candidates from the analytics layer: playoff swings,
    standings movement, streaks, positional movement, record/all-play
    divergence. Nothing surfaces without games behind it."""
    out: list[dict] = []
    scores = weekly_scores(storage, league.league_id, week)
    weeks_played = max((len(v) for v in scores.values()), default=0)
    if not weeks_played:
        return out

    def cand(cid, headline, facts, sections):
        out.append({"candidate_id": f"analytics:{cid}", "category": "analytics",
                    "headline": headline, "facts": facts, "score": 60,
                    "evidence": [f"computed:analytics:{league.slug}:{season}:{cid}"],
                    "recommended_sections": sections})

    for rid, notes in snapshot_deltas(storage, league, season, weeks_played).items():
        nm = names.get(rid, f"Roster {rid}")
        for n in notes:
            if n.startswith("playoff odds"):
                cand(f"odds:{rid}", f"{nm} playoff outlook moved: {n}", [n],
                     ["lowdown", "tracks"])
            elif n.startswith("standings"):
                cand(f"standings:{rid}", f"{nm} moved in the standings ({n})", [n],
                     ["lowdown", "tracks"])
            elif "room" in n:
                cand(f"pos:{rid}:{n.split()[0]}", f"{nm} positional shift: {n}", [n],
                     ["tracks", "forceflow"])
    for rid, st in scoring_streaks(storage, league, weeks_played).items():
        nm = names.get(rid, f"Roster {rid}")
        cand(f"streak:{rid}", f"{nm}: {st['length']} straight weeks of {st['kind']}",
             [f"{st['length']} consecutive weeks {st['kind']}"],
             ["tracks" if "top" in st["kind"] else "fades"])
    ap = all_play(scores)
    rosters = {r["roster_id"]: team_record(r) for r in storage.get_rosters(league.league_id)}
    for rid, rec in rosters.items():
        a = ap.get(rid)
        if not a or weeks_played < 3:
            continue
        real = rec["wins"] / max(1, rec["wins"] + rec["losses"])
        underlying = a["wins"] / max(1, a["wins"] + a["losses"])
        if abs(real - underlying) >= 0.3:
            nm = names.get(rid, f"Roster {rid}")
            direction = ("record flatters the performance" if real > underlying
                         else "record undersells the performance")
            cand(f"divergence:{rid}",
                 f"{nm}: {direction} ({rec['wins']}-{rec['losses']} vs "
                 f"{a['wins']}-{a['losses']} all-play)",
                 [f"all-play {a['wins']}-{a['losses']}"],
                 ["fades" if real > underlying else "tracks"])
    return out
