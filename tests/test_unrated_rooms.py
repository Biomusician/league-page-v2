"""A rank with no score behind it is roster_id order wearing a number.

`pre = max(0.0, 250.0 - rank)` clips every player past the end of the
reference board to exactly 0.0 — the same value as an unranked player and
as no player at all. A room built entirely of those scores zero, ties
every other zero room, and the stable sort hands the lowest roster_id the
better rank. The site then printed "#9 of 10" about it as a fact.

Not hypothetical: 16% of The Surfeit's drafted picks sit past the end of
the reference board, most of them kickers and defenses.
"""
from __future__ import annotations

from leaguepage.model_views import _room_contrasts, scout_view
from leaguepage.team_analytics import is_rated
from leaguepage.team_briefing import _best_and_worst


def _profile(scores):
    """scores: {rid: {pos: room_score}} -> a profile shaped like the real one."""
    positions = sorted({p for v in scores.values() for p in v})
    teams, ranks = {}, {}
    for rid, rooms in scores.items():
        teams[rid] = {p: {"room_score": rooms[p], "starter_value": rooms[p],
                          "depth_value": 0.0, "starters_used": 1,
                          "fragility": 0.0, "top_player": "x",
                          "count": 0 if rooms[p] == 0 else 2}
                      for p in positions}
    for pos in positions:
        order = sorted(teams, key=lambda rid: -teams[rid][pos]["room_score"])
        ranks[pos] = {rid: i + 1 for i, rid in enumerate(order)}
    return {
        "stage": "preseason consensus ranks", "positions": positions,
        "teams": teams, "ranks": ranks,
        "starter_ranks": ranks, "depth_ranks": ranks,
        "rated": {pos: {rid for rid in teams
                        if teams[rid][pos]["count"] and teams[rid][pos]["room_score"] > 0}
                  for pos in positions},
        "n": len(teams),
    }


def test_a_room_that_scored_nothing_is_not_rated():
    p = _profile({1: {"QB": 90.0, "RB": 40.0},
                  2: {"QB": 0.0, "RB": 0.0},
                  3: {"QB": 0.0, "RB": 0.0}})
    assert is_rated(p, "QB", 1)
    assert not is_rated(p, "QB", 2)
    assert not is_rated(p, "QB", 3)
    # ...and the ranks that separate 2 from 3 are roster_id order
    assert p["ranks"]["QB"][2] == 2 and p["ranks"]["QB"][3] == 3


def test_the_briefing_names_no_strength_for_an_unmeasured_roster():
    p = _profile({1: {"QB": 90.0, "RB": 40.0},
                  2: {"QB": 0.0, "RB": 0.0},
                  3: {"QB": 10.0, "RB": 80.0}})
    best, worst = _best_and_worst(p, 2)
    assert best is None and worst is None
    # the team with real rooms still gets its briefing
    best1, worst1 = _best_and_worst(p, 1)
    assert best1["pos"] == "QB" and worst1["pos"] == "RB"


def test_a_contrast_between_two_unmeasured_rooms_is_not_a_contrast():
    p = _profile({1: {"QB": 0.0}, 2: {"QB": 0.0}, 3: {"QB": 0.0},
                  4: {"QB": 0.0}, 5: {"QB": 90.0}})
    # 1 vs 4 is a four-rank "gap" and both rooms scored the same nothing
    assert _room_contrasts(p, 1, 4, "A", "D") == []
    # a scored room against an unscored one is a real contrast: zero really
    # does sort below every room the board could rate
    assert _room_contrasts(p, 5, 4, "E", "D")


def test_scout_view_does_not_credit_a_top_room_that_has_no_score():
    p = _profile({1: {"QB": 0.0, "RB": 0.0}, 2: {"QB": 0.0, "RB": 0.0}})
    matchup = {"teams": [{"roster_id": 1}, {"roster_id": 2}], "h2h": {}}
    view = scout_view(matchup, profile=p, names={1: "A", 2: "B"}, tags=[],
                      moves_by_rid={})
    assert view is None or not any("top room" in w for w in view["why"])


def test_a_profile_built_before_rated_existed_behaves_as_it_did():
    """Older snapshots and callers that never recorded which rooms were
    measured keep the previous answer rather than silently going quiet."""
    p = _profile({1: {"QB": 90.0}, 2: {"QB": 0.0}})
    del p["rated"]
    assert is_rated(p, "QB", 1) and is_rated(p, "QB", 2)
