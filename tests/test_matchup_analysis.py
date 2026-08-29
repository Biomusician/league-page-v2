from __future__ import annotations

from leaguepage.matchup_analysis import analyze_week, optimal_points

from fixtures import TEST_LEAGUE, populate_league, populate_matchups, set_records


def test_ten_team_week_pairs_five_matchups(storage):
    populate_league(storage, teams=10, rounds=3, picks="complete")
    populate_matchups(storage, week=1, teams=10)
    a = analyze_week(storage, TEST_LEAGUE, 1)
    assert len(a["matchups"]) == 5
    assert a["total_teams"] == 10


def test_twelve_team_week_pairs_six_matchups(storage):
    populate_league(storage, teams=12, rounds=3, picks="complete")
    populate_matchups(storage, week=1, teams=12)
    a = analyze_week(storage, TEST_LEAGUE, 1)
    assert len(a["matchups"]) == 6


def test_no_matchup_data_returns_none(storage):
    populate_league(storage, teams=10, rounds=3, picks="none")
    assert analyze_week(storage, TEST_LEAGUE, 1) is None


def test_projection_unavailable_is_declared_not_fabricated(storage):
    populate_league(storage, teams=10, rounds=3, picks="complete")
    populate_matchups(storage, week=1, teams=10)
    a = analyze_week(storage, TEST_LEAGUE, 1)
    m = a["matchups"][0]
    assert m["projection"]["a"] is None and m["projection"]["margin"] is None
    assert any("projected_score" in u for u in m["unavailable"])
    assert any("games not yet played" in u for u in m["unavailable"])


def test_co_managed_team_appears_in_matchup(storage):
    populate_league(storage, teams=10, rounds=3, picks="complete", co_managed_roster=1)
    populate_matchups(storage, week=1, teams=10)
    a = analyze_week(storage, TEST_LEAGUE, 1)
    t = a["matchups"][0]["teams"][0]
    assert t["co_managed"] and len(t["display_names"]) == 2


def test_history_and_streaks_from_played_weeks(storage):
    populate_league(storage, teams=10, rounds=3, picks="complete")
    # weeks 1-3 played: roster 1 beats roster 2 every time
    for wk in range(1, 4):
        populate_matchups(storage, week=wk, teams=10,
                          scores={rid: (150 - rid) if rid != 2 else 80 for rid in range(1, 11)})
    populate_matchups(storage, week=4, teams=10)  # upcoming
    set_records(storage, records={1: (3, 0, 447.0), 2: (0, 3, 240.0)})
    a = analyze_week(storage, TEST_LEAGUE, 4)
    m = a["matchups"][0]
    assert m["h2h"]["record"] == {1: 3, 2: 0}
    assert m["h2h"]["last_meeting"]["week"] == 3
    t1 = m["teams"][0]
    assert t1["streak"] == "W3"
    assert t1["all_play"]["wins"] == 27  # beat all 9 others, 3 weeks running
    assert t1["record"]["wins"] == 3
    assert a["weeks_played"] == 3


def test_no_history_states_absence(storage):
    populate_league(storage, teams=10, rounds=3, picks="complete")
    populate_matchups(storage, week=1, teams=10)
    a = analyze_week(storage, TEST_LEAGUE, 1)
    m = a["matchups"][0]
    assert m["h2h"]["meetings"] == [] and m["h2h"]["last_meeting"] is None


def test_optimal_points_greedy_with_flex():
    roster = ["QB", "RB", "WR", "FLEX", "BN", "BN"]
    players = [
        {"position": "QB", "points": 20.0},
        {"position": "RB", "points": 15.0},
        {"position": "RB", "points": 12.0},
        {"position": "WR", "points": 10.0},
        {"position": "TE", "points": 8.0},
    ]
    # QB20 + RB15 + WR10 + best flex remaining (RB12) = 57
    assert optimal_points(roster, players) == 57.0
    assert optimal_points(roster, []) is None


def test_every_matchup_has_evidence(storage):
    populate_league(storage, teams=10, rounds=3, picks="complete")
    populate_matchups(storage, week=1, teams=10)
    a = analyze_week(storage, TEST_LEAGUE, 1)
    prefixes = ("sleeper:", "adp:", "computed:", "archive:", "editorial:", "take:")
    for m in a["matchups"]:
        assert m["evidence"]
        assert all(e.startswith(prefixes) for e in m["evidence"])
