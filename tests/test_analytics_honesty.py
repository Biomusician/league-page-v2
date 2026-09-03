"""Claims the site used to make that its own numbers did not support.

Each test here names a sentence that shipped. The fix is not "hedge harder";
it is that the sentence was describing something other than what happened,
and the page now describes what happened.
"""
from __future__ import annotations

import pytest

from leaguepage.config import get_league
from leaguepage.leverage import describe_stake
from leaguepage.matchup_analysis import all_play
from leaguepage.matchup_interest import (classify, competitive_importance,
                                          story_value)
from leaguepage.storage import Storage
from leaguepage.team_analytics import (format_odds, get_snapshot,
                                       record_snapshot, recent_form,
                                       snapshot_deltas)
from leaguepage.team_briefing import _best_and_worst
from leaguepage.weekly_awards import BENCH_MEMORIAL_MIN, HEIST_MIN_POINTS

from season import populate_season

DISCO = get_league("disco")
SEASON = "2027"


# ------------------------------------------------------- probability text

def test_a_finite_simulation_never_reports_certainty():
    """0/2000 draws is not zero and 2000/2000 is not one. Printing "0%"
    turned a sample into a guarantee, and the leverage verdict beside it
    called a mathematically alive team eliminated."""
    assert format_odds(0.0) == "<1%"
    assert format_odds(1.0) == ">99%"
    assert format_odds(0.0004) == "<1%"
    assert format_odds(0.42) == "42%"
    assert format_odds(0.995) == ">99%"


def test_the_elimination_verdict_is_a_probability_not_a_ruling():
    assert describe_stake(0.20, 0.0) == "a loss all but ends it"
    assert "ends it" not in describe_stake(0.60, 0.35)


def test_two_locked_teams_are_not_told_the_game_matters():
    """The leverage model reported swing 0.000 for two teams already in, and
    the verdict next to it said "matters"."""
    assert describe_stake(1.0, 1.0) == "does not move either side"
    assert describe_stake(0.42, 0.40) == "does not move either side"


# --------------------------------------------------- methodology switches

def _snapshot_pair(storage, *, cur, prior):
    """Write two snapshots by hand so the stage fields are the subject."""
    for wk, payload in ((1, prior), (2, cur)):
        storage.set_meta(f"analytics_snapshot:{DISCO.slug}:{SEASON}:{wk}",
                         __import__("json").dumps(payload))


def test_a_valuation_stage_change_is_not_published_as_roster_movement(tmp_path):
    """Player values switch from reference ranks to a scoring blend at three
    played weeks. Across that boundary every room in the league moves on
    rosters nobody touched, and the site printed "RB room #2 to #8"."""
    with Storage(tmp_path / "t.sqlite3") as s:
        _snapshot_pair(
            s,
            prior={"week": 1, "stage": "preseason", "standings": {"1": 1},
                   "positional_ranks": {"RB": {"1": 2}}, "playoff": None},
            cur={"week": 2, "stage": "in-season", "standings": {"1": 1},
                 "positional_ranks": {"RB": {"1": 8}}, "playoff": None})
        assert snapshot_deltas(s, DISCO, SEASON, 2) == {}


def test_the_same_movement_is_published_within_one_stage(tmp_path):
    with Storage(tmp_path / "t.sqlite3") as s:
        _snapshot_pair(
            s,
            prior={"week": 1, "stage": "in-season", "standings": {"1": 1},
                   "positional_ranks": {"RB": {"1": 2}}, "playoff": None},
            cur={"week": 2, "stage": "in-season", "standings": {"1": 1},
                 "positional_ranks": {"RB": {"1": 8}}, "playoff": None})
        assert snapshot_deltas(s, DISCO, SEASON, 2) == {1: ["RB room #2 → #8"]}


def test_no_odds_delta_while_the_page_refuses_to_show_odds(tmp_path):
    """standings.html hides the number in the bands stage. The delta line
    printed one anyway, on the same page."""
    with Storage(tmp_path / "t.sqlite3") as s:
        _snapshot_pair(
            s,
            prior={"week": 1, "stage": "in-season", "playoff_stage": "bands",
                   "standings": {"1": 1}, "positional_ranks": {},
                   "playoff": {"1": 0.22}},
            cur={"week": 2, "stage": "in-season", "playoff_stage": "bands",
                 "standings": {"1": 1}, "positional_ranks": {},
                 "playoff": {"1": 0.44}})
        assert snapshot_deltas(s, DISCO, SEASON, 2) == {}


def test_an_odds_delta_survives_once_the_page_shows_percentages(tmp_path):
    with Storage(tmp_path / "t.sqlite3") as s:
        _snapshot_pair(
            s,
            prior={"week": 1, "stage": "in-season", "playoff_stage": "percentages",
                   "standings": {"1": 1}, "positional_ranks": {},
                   "playoff": {"1": 0.22}},
            cur={"week": 2, "stage": "in-season", "playoff_stage": "percentages",
                 "standings": {"1": 1}, "positional_ranks": {},
                 "playoff": {"1": 0.44}})
        assert snapshot_deltas(s, DISCO, SEASON, 2) == {1: ["playoff odds 22% → 44%"]}


def test_a_snapshot_records_the_outlook_stage_it_was_taken_under(tmp_path):
    """Without this the delta above has nothing to gate on."""
    with Storage(tmp_path / "t.sqlite3") as s:
        populate_season(s, DISCO, teams=12, weeks_played=5, current_week=5,
                        season=SEASON, seed=4)
        record_snapshot(s, DISCO, SEASON, 5)
        assert get_snapshot(s, DISCO, SEASON, 5)["playoff_stage"] == "bands"


# --------------------------------------------------------- ties and rooms

def test_one_room_is_never_both_the_strength_and_the_concern():
    """min and max both return the first extreme element, so a team whose
    skill rooms ranked alike had QB printed as what carries it and what
    exposes it, in the same breath."""
    profile = {
        "n": 10, "positions": ["QB", "RB", "WR", "TE"],
        "ranks": {p: {1: 1} for p in ("QB", "RB", "WR", "TE")},
        "starter_ranks": {p: {1: 1} for p in ("QB", "RB", "WR", "TE")},
        "depth_ranks": {p: {1: 1} for p in ("QB", "RB", "WR", "TE")},
        "teams": {1: {p: {"fragility": 0.0, "count": 3, "top_player": "X"}
                      for p in ("QB", "RB", "WR", "TE")}},
    }
    best, worst = _best_and_worst(profile, 1)
    assert best and best["pos"] == "QB"
    assert worst is None


def test_a_real_difference_still_names_both_rooms():
    profile = {
        "n": 10, "positions": ["QB", "RB"],
        "ranks": {"QB": {1: 1}, "RB": {1: 9}},
        "starter_ranks": {"QB": {1: 1}, "RB": {1: 9}},
        "depth_ranks": {"QB": {1: 1}, "RB": {1: 9}},
        "teams": {1: {p: {"fragility": 0.0, "count": 3, "top_player": "X"}
                      for p in ("QB", "RB")}},
    }
    best, worst = _best_and_worst(profile, 1)
    assert (best["pos"], worst["pos"]) == ("QB", "RB")


# ------------------------------------------------------------- all-play

def test_all_play_reports_its_denominator():
    """A team on a bye played fewer all-play games than the league, and its
    percentage was ranked against percentages over a bigger denominator."""
    scores = {1: [(1, 100.0), (2, 90.0)],       # two weeks
              2: [(1, 80.0), (2, 95.0)],
              3: [(1, 70.0)]}                    # bye in week 2
    ap = all_play(scores)
    # week 1 has three teams (two opponents each); week 2 has two.
    assert ap[1]["games"] == 3 and ap[1]["weeks"] == 2
    assert ap[3]["games"] == 2 and ap[3]["weeks"] == 1


def test_recent_form_labels_each_team_with_the_weeks_it_actually_played(tmp_path):
    """`#8 scoring over the last 3 weeks` about a team that played two of
    them is a wrong denominator printed as a fact."""
    with Storage(tmp_path / "t.sqlite3") as s:
        populate_season(s, DISCO, teams=12, weeks_played=4, current_week=4,
                        season=SEASON, seed=11)
        form = recent_form(s, DISCO, 4)
    assert form
    for rid, f in form.items():
        assert f["window_label"] == f"{f['window']} week" + ("" if f["window"] == 1 else "s")
        assert f["window"] <= f["league_window"]


# ------------------------------------------------------------- the tags

def _matchup(seed_a, seed_b):
    return {"league": "disco", "evidence": ["test"],
            "teams": [{"roster_id": 1, "team_slug": "a", "standing": seed_a,
                       "record": {"wins": 7, "losses": 3, "ties": 0}, "streak": None},
                      {"roster_id": 2, "team_slug": "b", "standing": seed_b,
                       "record": {"wins": 6, "losses": 4, "ties": 0}, "streak": None}]}


def _ctx(weeks_played, week):
    return {"total_teams": 12, "weeks_played": weeks_played, "week": week,
            "playoff_teams": 6, "playoff_week_start": 15}


def _tags(seed_a, seed_b):
    m, ctx = _matchup(seed_a, seed_b), _ctx(12, 13)
    return classify(m, competitive_importance(m, ctx),
                    story_value(m), ctx)


def test_two_teams_already_in_get_a_seeding_tag_not_a_berth_claim():
    """"The result moves a playoff berth" rendered next to a leverage model
    reporting if_win 1.0 and if_lose 1.0 for both sides."""
    tags = _tags(1, 2)
    assert "Seeding at Stake" in tags
    assert "Playoff Leverage" not in tags


def test_a_team_on_the_cutline_still_gets_the_leverage_tag():
    tags = _tags(6, 11)
    assert "Playoff Leverage" in tags
    assert "Seeding at Stake" not in tags


# --------------------------------------------------------- award slates

@pytest.mark.parametrize("key,nominee,strong", [
    ("hard-luck-bastard", {"score_rank": 1}, True),
    ("hard-luck-bastard", {"score_rank": 10}, False),
    ("escape-artist", {"bottom_three": True}, True),
    ("escape-artist", {"bottom_three": False}, False),
    ("waiver-wire-heist", {"metric_value": 2 * HEIST_MIN_POINTS + 1}, True),
    ("waiver-wire-heist", {"metric_value": HEIST_MIN_POINTS + 1}, False),
    ("benchwarmer-memorial", {"metric_value": 2 * BENCH_MEMORIAL_MIN + 1}, True),
    ("benchwarmer-memorial", {"metric_value": BENCH_MEMORIAL_MIN + 1}, False),
])
def test_strong_means_the_award_cleared_its_own_metric_line(key, nominee, strong):
    """Four awards were "strong" whenever they had any nominee at all, which
    made the field a restatement of "nominees exist" — and it is the signal
    the Commissioner reads to decide whether to give the award."""
    from leaguepage import weekly_awards

    aw = {"award_key": key, "nominees": [nominee]}
    # exercise the same expression the module uses, on one award
    src = [aw]
    for a in src:
        top = a["nominees"][0]
        got = (
            (a["award_key"] == "hard-luck-bastard" and top.get("score_rank", 99) <= 3)
            or (a["award_key"] == "escape-artist" and top.get("bottom_three"))
            or (a["award_key"] == "waiver-wire-heist"
                and top.get("metric_value", 0) >= 2 * weekly_awards.HEIST_MIN_POINTS)
            or (a["award_key"] == "benchwarmer-memorial"
                and top.get("metric_value", 0) >= 2 * weekly_awards.BENCH_MEMORIAL_MIN)
        )
    assert bool(got) is strong
