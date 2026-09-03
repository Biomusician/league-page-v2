"""The longitudinal half of the transaction ledger.

The at-the-time rationale is never rewritten. This answers a different
question, later: did the thing he was reaching for actually arrive?
"""
from __future__ import annotations

from leaguepage.config import get_league
from leaguepage.transaction_analysis import aged_line, how_it_aged

from fixtures import add_players, populate_league, populate_matchups

DISCO = get_league("disco")
SEASON = "2027"


def _env(storage):
    populate_league(storage, DISCO, teams=12, season=SEASON)
    add_players(storage, {"added": ("New Back", "RB", 40),
                          "cut": ("Old Back", "RB", 90)})
    return {"txn_id": "t1", "week": 1, "type": "waiver", "rids": [3],
            "adds": [{"pid": "added", "rid": 3, "name": "New Back", "pos": "RB"}],
            "drops": [{"pid": "cut", "rid": 3, "name": "Old Back", "pos": "RB"}]}


def _week(storage, wk, *, points, starters):
    populate_matchups(storage, DISCO, week=wk, teams=12,
                      scores={rid: 100.0 + rid for rid in range(1, 13)},
                      players_points=points, starters=starters)


def test_nothing_is_claimed_before_a_week_has_passed(storage):
    row = _env(storage)
    assert how_it_aged(storage, DISCO, row, through_week=1) is None


def test_the_added_player_is_read_on_the_roster_that_added_him(storage):
    row = _env(storage)
    for wk in (2, 3, 4):
        _week(storage, wk, points={3: {"added": 14.0}}, starters={3: ["added"]})
    aged = how_it_aged(storage, DISCO, row, through_week=4)
    assert aged["added"][0]["starts"] == 3
    assert aged["added"][0]["points"] == 42.0
    assert "started 3 of 3 weeks since, for 42 points" in aged_line(aged)


def test_the_dropped_player_is_followed_wherever_he_went(storage):
    """The interesting question about a drop is what he did next, and for
    whom. Only the add side was ever read."""
    row = _env(storage)
    for wk in (2, 3):
        _week(storage, wk, points={3: {"added": 2.0}, 7: {"cut": 20.0}},
              starters={3: [], 7: ["cut"]})
    names = {7: "Corn-Fed Fatties"}
    aged = how_it_aged(storage, DISCO, row, through_week=3, names=names)
    dropped = aged["dropped"][0]
    assert dropped["points"] == 40.0
    assert dropped["claimed_by"] == "Corn-Fed Fatties"
    assert "Old Back has scored 40 for Corn-Fed Fatties" in aged_line(aged)


def test_a_player_nobody_rostered_is_not_reported(storage):
    row = _env(storage)
    for wk in (2, 3):
        _week(storage, wk, points={3: {"added": 8.0}}, starters={3: ["added"]})
    aged = how_it_aged(storage, DISCO, row, through_week=3)
    assert aged["dropped"] == []


def test_the_room_it_was_aimed_at_is_checked(storage):
    """Nothing re-read the positional room afterwards, so 'did the weakness
    get solved' was never answered."""
    row = _env(storage)
    for wk in (2, 3):
        _week(storage, wk, points={3: {"added": 9.0}}, starters={3: ["added"]})
    profile = {"ranks": {"RB": {3: 4}}}
    context = {"adds": {"added": {"pos": "RB", "before": 12, "after": 12}}}
    aged = how_it_aged(storage, DISCO, row, through_week=3,
                       profile=profile, context=context)
    assert aged["room"] == {"position": "RB", "before": 12, "now": 4, "solved": True}
    assert "the RB room went #12 to #4" in aged_line(aged)


def test_a_room_that_did_not_move_says_so(storage):
    row = _env(storage)
    for wk in (2, 3):
        _week(storage, wk, points={3: {"added": 1.0}}, starters={3: []})
    profile = {"ranks": {"RB": {3: 12}}}
    context = {"adds": {"added": {"pos": "RB", "before": 12, "after": 12}}}
    aged = how_it_aged(storage, DISCO, row, through_week=3,
                       profile=profile, context=context)
    assert aged["room"]["solved"] is False
    assert "still #12" in aged_line(aged)
