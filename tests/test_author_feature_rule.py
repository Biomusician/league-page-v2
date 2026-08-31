"""The author does not headline his own newsletter without playoff stakes."""
from __future__ import annotations

import pytest

from leaguepage.matchup_interest import (
    author_matchup_stakes, recommend_prominence,
)


def _m(name, ci, sv, **extra):
    return {"matchup": {"matchup_slug": name},
            "competitive_importance": {"score": ci},
            "story_value": {"score": sv}, **extra}


def _analysis(*, week, played, spots=6, start=15):
    return {"week": week, "weeks_played": played,
            "playoff_teams": spots, "playoff_week_start": start}


def _matchup(seed_a, seed_b):
    return {"teams": [{"roster_id": 1, "standing": seed_a},
                      {"roster_id": 7, "standing": seed_b}]}


# ------------------------------------------------------------- stakes rule

def test_no_stakes_early_season():
    ok, why = author_matchup_stakes(_matchup(1, 2), _analysis(week=2, played=2))
    assert not ok and "week(s) played" in why


def test_no_stakes_midseason_even_with_good_records():
    # week 8 of a week-15 playoff start: too early to call anything decisive
    ok, why = author_matchup_stakes(_matchup(1, 2), _analysis(week=8, played=8))
    assert not ok and "outside the last" in why


def test_stakes_on_the_bubble_late():
    ok, why = author_matchup_stakes(_matchup(6, 9), _analysis(week=13, played=12))
    assert ok and "cutline" in why


def test_stakes_when_both_teams_hold_berths_late():
    ok, why = author_matchup_stakes(_matchup(1, 2), _analysis(week=14, played=13))
    assert ok and "seeding is live" in why


def test_no_stakes_late_when_both_teams_are_eliminated_range():
    ok, why = author_matchup_stakes(_matchup(9, 10), _analysis(week=14, played=13))
    assert not ok and "near the playoff cutline" in why


def test_ten_team_league_cutline_moves_with_settings():
    # 4-team playoff: seed 6 is nowhere near the cutline
    ok, _ = author_matchup_stakes(_matchup(6, 7), _analysis(week=13, played=12, spots=4))
    assert not ok
    ok, _ = author_matchup_stakes(_matchup(4, 5), _analysis(week=13, played=12, spots=4))
    assert ok


# -------------------------------------------------------- prominence effect

def test_blocked_author_matchup_yields_the_feature():
    scored = [
        _m("author-vs-x", 60, 40, feature_blocked="the author's own matchup, held out"),
        _m("b-vs-c", 50, 30),
        _m("d-vs-e", 20, 10),
    ]
    recommend_prominence(scored)
    by = {m["matchup"]["matchup_slug"]: m["recommended_prominence"] for m in scored}
    assert by["b-vs-c"] == "FEATURE"          # someone else headlines
    assert by["author-vs-x"] == "MAJOR"       # still prominent, just not the headline
    assert by["d-vs-e"] == "MAJOR"


def test_author_matchup_with_stakes_still_features():
    scored = [_m("author-vs-x", 60, 40, author_stakes="cutline"), _m("b-vs-c", 50, 30)]
    recommend_prominence(scored)
    by = {m["matchup"]["matchup_slug"]: m["recommended_prominence"] for m in scored}
    assert by["author-vs-x"] == "FEATURE"


def test_ordering_is_otherwise_untouched():
    scored = [_m("a", 90, 0), _m("b", 80, 0), _m("c", 70, 0), _m("d", 10, 0)]
    recommend_prominence(scored)
    by = {m["matchup"]["matchup_slug"]: m["recommended_prominence"] for m in scored}
    assert by == {"a": "FEATURE", "b": "MAJOR", "c": "MAJOR", "d": "STANDARD"}


def test_all_blocked_still_produces_a_feature():
    scored = [_m("a", 90, 0, feature_blocked="x"), _m("b", 80, 0, feature_blocked="x")]
    recommend_prominence(scored)
    assert any(m["recommended_prominence"] == "FEATURE" for m in scored)


# ------------------------------------------------------- end-to-end on real config

def test_real_leagues_declare_the_author_roster():
    from leaguepage.config import LEAGUES

    assert all(lg.author_roster_id for lg in LEAGUES)


def test_week_one_author_matchup_is_not_featured(storage):
    """Full path through compute_week with the author's team scoring highest."""
    from leaguepage.config import League
    from leaguepage.matchup_packet import compute_week

    from fixtures import populate_league, populate_matchups, set_records

    lg = League(slug="testleague", display_name="TEST LEAGUE", league_id="TEST123",
                theme="disco", subtitle="t", adp_source="", author_roster_id=1)
    populate_league(storage, lg, teams=10, rounds=3, picks="complete")
    populate_matchups(storage, lg, week=1, teams=10)
    # roster 1 (the author) sits atop the table; 2 is right behind
    set_records(storage, lg, records={1: (0, 0, 0), 2: (0, 0, 0)})
    computed = compute_week(storage, lg, 1)
    mine = [m for m in computed["scored"]
            if any(t["roster_id"] == 1 for t in m["matchup"]["teams"])][0]
    assert mine.get("feature_blocked")
    assert mine["recommended_prominence"] != "FEATURE"
    assert any(m["recommended_prominence"] == "FEATURE" for m in computed["scored"])
