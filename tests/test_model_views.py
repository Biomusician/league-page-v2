"""Scout View, Model Board and the no-dead-ends rule.

Two things are being defended here at once, and they pull against each
other: every primary route must have something real behind it, and nothing
computed may ever pass itself off as the Commissioner. A fallback that
invents a joke to fill a page is worse than the empty page it replaced.
"""
from __future__ import annotations

import pytest

from leaguepage import model_views as mv

NAMES = {1: "Los Bandidos", 2: "Wild SeeKats", 3: "Dave", 4: "Gary"}
SLUGS = {rid: f"t{rid}" for rid in NAMES}
POSITIONS = ["QB", "RB", "WR", "TE", "K", "DEF"]


def profile(ranks=None, n=4):
    ranks = ranks or {
        "QB": {1: 1, 2: 3, 3: 4, 4: 2},
        "RB": {1: 4, 2: 1, 3: 2, 4: 3},
        "WR": {1: 1, 2: 4, 3: 3, 4: 2},
        "TE": {1: 3, 2: 1, 3: 2, 4: 4},
        "K": {1: 1, 2: 2, 3: 3, 4: 4},
        "DEF": {1: 4, 2: 3, 3: 2, 4: 1},
    }
    return {"n": n, "positions": POSITIONS, "ranks": ranks,
            "teams": {rid: {} for rid in NAMES}}


def matchup(a=1, b=2, h2h=None):
    return {"matchup_slug": f"t{a}-vs-t{b}",
            "teams": [{"roster_id": a}, {"roster_id": b}],
            "h2h": h2h or {"record": {a: 0, b: 0}, "meetings": [],
                           "last_meeting": None}}


def standings(order=(1, 2, 3, 4), played=False):
    return [{"roster_id": rid, "wins": 3 if played else 0,
             "losses": 1 if played else 0, "pf": 400 - i * 25 if played else 0}
            for i, rid in enumerate(order)]


# ------------------------------------------------------------ Scout View

def test_scout_view_reports_room_contrast_and_history():
    sv = mv.scout_view(
        matchup(h2h={"record": {1: 2, 2: 1},
                     "meetings": [{"week": 3, "points": {1: 110.2, 2: 99.0},
                                   "winner": 1}],
                     "last_meeting": {"week": 3, "points": {1: 110.2, 2: 99.0},
                                      "winner": 1}}),
        profile=profile(), names=NAMES, tags=["Coalition Warfare"],
        moves_by_rid={}, recap_by_rid={})
    assert sv is not None
    joined = " ".join(sv["why"] + sv["watch"])
    assert "WR" in joined and "RB" in joined
    assert "Head to head" in joined and "2–1" in joined
    assert "Last meeting, week 3" in joined


def test_scout_view_never_writes_in_the_commissioners_voice():
    sv = mv.scout_view(matchup(), profile=profile(), names=NAMES,
                       tags=["Coalition Warfare"], moves_by_rid={}, recap_by_rid={})
    text = " ".join(sv["why"] + sv["watch"] + [sv["note"]]).lower()
    for tell in ("woof", "roast", "should win", "will win", "my money",
                 "the favorite", "i think", "lock of the week", "destroy"):
        assert tell not in text
    assert "not the Commissioner's preview" in sv["note"]


def test_scout_view_ignores_kicker_gaps():
    ranks = {p: {rid: 1 for rid in NAMES} for p in POSITIONS}
    ranks["K"] = {1: 1, 2: 4, 3: 2, 4: 3}     # a four-spot kicker gap
    sv = mv.scout_view(matchup(), profile=profile(ranks=ranks), names=NAMES,
                       tags=[], moves_by_rid={}, recap_by_rid={})
    assert sv is None or not any("K:" in line for line in sv["why"])


def test_scout_view_returns_none_when_there_is_nothing_to_say():
    flat = {p: {rid: 2 for rid in NAMES} for p in POSITIONS}
    assert mv.scout_view(matchup(), profile=profile(ranks=flat), names=NAMES,
                         tags=[], moves_by_rid={}, recap_by_rid={}) is None


def test_scout_view_surfaces_a_questionable_move():
    sv = mv.scout_view(
        matchup(), profile=profile(), names=NAMES, tags=[],
        moves_by_rid={1: [{"week": 1, "line": "Added X · dropped Y",
                           "questionable": True}]},
        recap_by_rid={})
    assert any("flagged questionable" in line for line in sv["watch"])


# ----------------------------------------------------------- Model Board

def test_model_board_ranks_every_team_with_reasoning():
    board = mv.model_board(profile=profile(), names=NAMES, slugs=SLUGS,
                           standings=standings(), form={}, weeks_played=0)
    assert len(board["rows"]) == 4
    assert [r["rank"] for r in board["rows"]] == [1, 2, 3, 4]
    for r in board["rows"]:
        assert r["tier"] and r["strongest"] and r["weakest"] and r["factor"]
        assert r["slug"]


def test_model_board_says_what_it_is_made_of():
    pre = mv.model_board(profile=profile(), names=NAMES, slugs=SLUGS,
                         standings=standings(), form={}, weeks_played=0)
    assert "no games played yet" in pre["basis"]
    mid = mv.model_board(profile=profile(), names=NAMES, slugs=SLUGS,
                         standings=standings(played=True), form={},
                         weeks_played=6)
    assert "% roster construction" in mid["basis"] and "week 6" in mid["basis"]


def test_model_board_ignores_kickers_and_defenses():
    """A team that owns K and DEF and nothing else must not rank first."""
    ranks = {"QB": {1: 4, 2: 1, 3: 2, 4: 3}, "RB": {1: 4, 2: 1, 3: 2, 4: 3},
             "WR": {1: 4, 2: 1, 3: 2, 4: 3}, "TE": {1: 4, 2: 1, 3: 2, 4: 3},
             "K": {1: 1, 2: 4, 3: 3, 4: 2}, "DEF": {1: 1, 2: 4, 3: 3, 4: 2}}
    board = mv.model_board(profile=profile(ranks=ranks), names=NAMES,
                           slugs=SLUGS, standings=standings(), form={},
                           weeks_played=0)
    assert board["rows"][0]["roster_id"] == 2
    assert board["rows"][-1]["roster_id"] == 1


def test_results_take_over_from_construction_as_weeks_accumulate():
    """Roster construction says team 1 is best; scoring says team 4 is."""
    ranks = {p: {1: 1, 2: 2, 3: 3, 4: 4} for p in POSITIONS}
    st = [{"roster_id": rid, "wins": 0, "losses": 0, "pf": pf}
          for rid, pf in ((1, 100), (2, 200), (3, 300), (4, 400))]
    early = mv.model_board(profile=profile(ranks=ranks), names=NAMES,
                           slugs=SLUGS, standings=st, form={}, weeks_played=1)
    late = mv.model_board(profile=profile(ranks=ranks), names=NAMES,
                          slugs=SLUGS, standings=st, form={}, weeks_played=10)
    assert early["rows"][0]["roster_id"] == 1
    assert late["rows"][0]["roster_id"] == 4


def test_commissioner_ranking_keeps_the_model_as_a_comparison():
    board = mv.model_board(profile=profile(), names=NAMES, slugs=SLUGS,
                           standings=standings(), form={}, weeks_played=0)
    model_first = board["rows"][0]["roster_id"]
    ranking = [{"rank": 1, "roster_id": 3, "name": "Dave"},
               {"rank": 2, "roster_id": model_first, "name": "x"}]
    merged = mv.compare_to_commissioner(board, ranking)
    assert merged[0]["model_rank"] == next(
        r["rank"] for r in board["rows"] if r["roster_id"] == 3)
    assert merged[1]["model_rank"] == 1
    assert "higher" in merged[1]["model_gap_label"]


def test_model_board_is_empty_rather_than_wrong_with_no_profile():
    assert mv.model_board(profile={}, names=NAMES, slugs=SLUGS,
                          standings=[], form={}, weeks_played=0)["rows"] == []


# ------------------------------------------------------------- Black Box

def test_black_box_has_something_before_it_has_records():
    watching = mv.black_box_preview(
        profile=profile(), names=NAMES,
        reaches=[{"name": "Jahdae Walker", "team": "Stafford and Sons",
                  "dv": {"label": "REACH · 244 picks early"}}],
        steals=[], weeks_played=0)
    assert watching
    assert any("Largest departure" in w["label"] for w in watching)
    assert all(w["value"] and w["note"] for w in watching)


def test_black_box_stands_down_once_real_records_exist():
    assert mv.black_box_preview(profile=profile(), names=NAMES, reaches=[],
                                steals=[], weeks_played=3) == []
