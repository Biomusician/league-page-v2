"""Regressions for numbers that were quietly wrong.

Each test here pins one defect that produced a plausible-looking figure
rather than an error, which is the only kind worth writing a test for.
"""
from __future__ import annotations

import pytest

from leaguepage.config import get_league
from leaguepage.draft_value import SKILL_POSITIONS
from leaguepage.matchup_analysis import (all_play, faab_cost, optimal_points,
                                         season_efficiency)
from leaguepage.matchup_interest import (LEVERAGE_MIN_WEEKS,
                                         competitive_importance)
from leaguepage.storage import Storage
from leaguepage.team_analytics import (playoff_outlook, positional_profile,
                                       recent_form, record_snapshot,
                                       remaining_schedule,
                                       roster_contrast_lines, snapshot_deltas)
from leaguepage.weekly_awards import best_bench_swap

from fixtures import add_players
from season import populate_season

DISCO = get_league("disco")
SEASON = "2027"


# --------------------------------------------------------------- FAAB

def test_faab_cost_reads_the_field_sleeper_actually_uses():
    """A waiver claim's price lives in settings.waiver_bid. waiver_budget is
    budget moving between teams in a trade, and it is empty on a claim, so
    reading only that saw every claim in the league as free."""
    assert faab_cost({"settings": {"waiver_bid": 34}}) == 34
    assert faab_cost({"waiver_budget": [{"amount": 12}]}) == 12
    assert faab_cost({"settings": {"waiver_bid": 5},
                      "waiver_budget": [{"amount": 7}]}) == 12
    assert faab_cost({}) == 0
    assert faab_cost({"settings": {}, "waiver_budget": []}) == 0


# ------------------------------------------------------- special teams

def test_bench_swap_never_shames_a_manager_over_a_kicker(storage):
    """You start your only kicker. A benched one outscoring him is not a
    decision anybody made."""
    add_players(storage, {
        "k1": ("Started Kicker", "K", 300),
        "k2": ("Bench Kicker", "K", 310),
        "r1": ("Started Back", "RB", 20),
        "r2": ("Bench Back", "RB", 25),
    })
    row = {"starters": ["k1", "r1"],
           "players_points": {"k1": 3.0, "k2": 19.0, "r1": 9.0, "r2": 11.0}}
    swap = best_bench_swap(storage, ["K", "RB"], row)
    assert swap is not None
    assert swap["slot"] == "RB"          # the kicker slot was skipped entirely
    assert "Kicker" not in swap["benched"]


def test_roster_contrast_uses_skill_rooms(storage):
    populate_season(storage, DISCO, teams=12, weeks_played=0, season=SEASON)
    profile = positional_profile(storage, DISCO)
    if not any(p not in SKILL_POSITIONS for p in profile["positions"]):
        pytest.skip("this league carries no special-teams rooms")
    lines = roster_contrast_lines(profile, 1, 2, "Alpha", "Beta")
    assert lines
    for line in lines:
        assert " K " not in line and "DEF" not in line


# --------------------------------------------------- the real schedule

def test_remaining_schedule_reads_future_pairings(storage):
    """Sleeper serves the whole regular season's pairings up front, with
    zero points until they are played."""
    populate_season(storage, DISCO, teams=12, weeks_played=4,
                    current_week=4, season=SEASON, playoff_week_start=15)
    sched = remaining_schedule(storage, DISCO.league_id, 5, 14)
    assert sorted(sched) == list(range(5, 15))
    for week, pairs in sched.items():
        assert len(pairs) == 6
        rids = [r for pair in pairs for r in pair]
        assert sorted(rids) == list(range(1, 13))     # nobody twice, nobody missing


def test_remaining_schedule_excludes_weeks_already_played(storage):
    populate_season(storage, DISCO, teams=12, weeks_played=4,
                    current_week=4, season=SEASON)
    sched = remaining_schedule(storage, DISCO.league_id, 1, 14)
    assert not any(wk <= 4 for wk in sched), "a played week is history, not schedule"


def test_remaining_schedule_ignores_unpaired_rows(storage):
    populate_season(storage, DISCO, teams=12, weeks_played=0, season=SEASON)
    rows = storage.get_matchups(DISCO.league_id, 3)
    for r in rows:
        r["matchup_id"] = None
    storage.save_matchups(DISCO.league_id, 3, rows)
    assert 3 not in remaining_schedule(storage, DISCO.league_id, 1, 14)


def test_playoff_model_simulates_the_actual_schedule(storage):
    populate_season(storage, DISCO, teams=12, weeks_played=6,
                    current_week=6, season=SEASON, playoff_week_start=15)
    out = playoff_outlook(storage, DISCO, 6, sims=200)
    assert out["remaining_weeks"] == 8
    assert out["schedule_weeks"] == 8
    assert "actual remaining schedule" in out["note"]


def test_playoff_model_says_so_when_the_schedule_is_missing(storage):
    populate_season(storage, DISCO, teams=12, weeks_played=6,
                    current_week=6, season=SEASON, playoff_week_start=15)
    for wk in range(7, 15):
        storage.save_matchups(DISCO.league_id, wk, [])
    out = playoff_outlook(storage, DISCO, 6, sims=200)
    assert out["schedule_weeks"] == 0
    assert "random league pairings" in out["note"]


def test_playoff_model_carries_points_already_scored(storage):
    """The tiebreak read records[rid]["points_for"], but team_record returns
    "fpts" -- so every simulated season started every team at zero and the
    last playoff spot was decided without the scoring that had happened."""
    populate_season(storage, DISCO, teams=12, weeks_played=6,
                    current_week=6, season=SEASON)
    base = playoff_outlook(storage, DISCO, 6, sims=400)

    rosters = storage.get_rosters(DISCO.league_id)
    worst = min(rosters, key=lambda r: (r["settings"]["wins"], r["settings"]["fpts"]))
    worst["settings"] = {**worst["settings"], "fpts": worst["settings"]["fpts"] + 900}
    storage.save_rosters(DISCO.league_id, rosters)
    bumped = playoff_outlook(storage, DISCO, 6, sims=400)

    rid = worst["roster_id"]
    assert bumped["teams"][rid]["odds"] > base["teams"][rid]["odds"], \
        "900 points of scoring changed nothing, so points-for is not being read"


# --------------------------------------------------------- snapshots

def test_week_zero_stores_no_standings_baseline(storage):
    """Preseason every team is 0-0 on 0 points, so the sort is roster_id
    order wearing a rank. Storing it made week 1 announce movement nobody
    made."""
    populate_season(storage, DISCO, teams=12, weeks_played=0, season=SEASON)
    snap = record_snapshot(storage, DISCO, SEASON, 0)
    assert snap["standings"] is None


def test_no_phantom_movement_against_a_preseason_baseline(storage):
    populate_season(storage, DISCO, teams=12, weeks_played=0, season=SEASON)
    record_snapshot(storage, DISCO, SEASON, 0)
    populate_season(storage, DISCO, teams=12, weeks_played=3, current_week=3,
                    season=SEASON)
    record_snapshot(storage, DISCO, SEASON, 3)
    deltas = snapshot_deltas(storage, DISCO, SEASON, 3)
    flat = [note for notes in deltas.values() for note in notes]
    assert not any(n.startswith("standings ") for n in flat), flat


# ------------------------------------------------------------ all-play

def test_windowed_all_play_compares_week_to_week(storage):
    """Collapsing the window to one pseudo-week cross-produced every score
    against every other, inflating the game count by the window size."""
    populate_season(storage, DISCO, teams=12, weeks_played=6,
                    current_week=6, season=SEASON)
    form = recent_form(storage, DISCO, 6, window=3)
    assert form
    row = next(iter(form.values()))
    ap = row["all_play"]
    assert ap["wins"] + ap["losses"] + ap.get("ties", 0) == 11 * 3


# -------------------------------------------------- lineup efficiency

def test_season_efficiency_sums_rather_than_averages_ratios(storage):
    """Averaging weekly ratios lets a 40-point week and a 140-point week
    count the same."""
    add_players(storage, {f"sp{i}": (f"Player {i}", "RB", 100 + i)
                          for i in range(0, 200)})
    populate_season(storage, DISCO, teams=12, weeks_played=3,
                    current_week=3, season=SEASON)
    slots = ["RB"] * 7 + ["BN"] * 4
    eff = season_efficiency(storage, slots, DISCO.league_id, 3)
    assert eff
    for rid, row in eff.items():
        assert row["weeks"] == 3
        assert 0 < row["pct"] <= 100.0
        assert row["left_on_bench"] == pytest.approx(
            round(row["optimal"] - row["actual"], 1), abs=0.2)


def test_efficiency_is_withheld_when_the_optimal_lineup_cannot_be_rebuilt(storage):
    """A player missing from the cached players dict has no position, so no
    slot will take him -- but his points still landed on the scoreboard.
    Reporting 214% efficiency is worse than reporting none."""
    add_players(storage, {"known": ("Known Back", "RB", 5)})
    row = {"roster_id": 1, "matchup_id": 1, "points": 40.0,
           "starters": ["known", "ghost"],
           "players_points": {"known": 10.0, "ghost": 30.0}}
    storage.save_matchups("L2", 1, [row])
    assert season_efficiency(storage, ["RB", "BN"], "L2", 1) == {}


def test_perfect_lineup_is_a_hundred_percent(storage):
    add_players(storage, {"a": ("A Back", "RB", 1), "b": ("B Back", "RB", 2)})
    row = {"roster_id": 1, "matchup_id": 1, "points": 20.0,
           "starters": ["a"], "players_points": {"a": 20.0, "b": 4.0}}
    storage.save_matchups("L", 1, [row])
    eff = season_efficiency(storage, ["RB", "BN"], "L", 1)
    assert eff[1]["pct"] == 100.0
    assert eff[1]["left_on_bench"] == 0.0
    assert optimal_points(["RB", "BN"], [{"position": "RB", "points": 20.0},
                                         {"position": "RB", "points": 4.0}]) == 20.0


# ------------------------------------------------------------ leverage

def _matchup(sa: int, sb: int) -> dict:
    return {"league": "disco", "evidence": ["test"],
            "teams": [{"roster_id": 1, "team_slug": "a", "standing": sa,
                       "record": {"wins": 7, "losses": 3, "ties": 0}, "streak": None},
                      {"roster_id": 2, "team_slug": "b", "standing": sb,
                       "record": {"wins": 6, "losses": 4, "ties": 0}, "streak": None}]}


def _ctx(weeks_played: int, week: int) -> dict:
    return {"total_teams": 12, "weeks_played": weeks_played, "week": week,
            "playoff_teams": 6, "playoff_week_start": 15}


def test_playoff_leverage_finally_fires():
    """The weight was defined and no component ever emitted it, so the
    Playoff Leverage tag could not exist."""
    ci = competitive_importance(_matchup(6, 7), _ctx(12, 13))
    labels = [c["label"] for c in ci["components"]]
    assert any("leverage" in lab for lab in labels), labels


def test_leverage_waits_for_a_table_worth_reading():
    ci = competitive_importance(_matchup(6, 7), _ctx(LEVERAGE_MIN_WEEKS - 1, 5))
    assert not any("leverage" in c["label"] for c in ci["components"])


def test_leverage_ignores_a_matchup_nowhere_near_the_line():
    ci = competitive_importance(_matchup(11, 12), _ctx(12, 13))
    assert not any("leverage" in c["label"] for c in ci["components"])
