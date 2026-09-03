"""What happened to the picks, without re-grading what they cost."""
from __future__ import annotations

from leaguepage.config import get_league
from leaguepage.draft_aging import (aging_line, departed_headliners,
                                    draft_aging, team_summary)

from fixtures import populate_league, populate_matchups, player_name

DISCO = get_league("disco")
SEASON = "2027"


def _drafted(storage, teams=12, rounds=3):
    populate_league(storage, DISCO, teams=teams, rounds=rounds, season=SEASON)
    rosters = storage.get_rosters(DISCO.league_id)
    picks = storage.get_draft_picks(f"D-{DISCO.league_id}")
    by_rid = {}
    for p in picks:
        by_rid.setdefault(p["roster_id"], []).append(p["player_id"])
    for r in rosters:
        r["players"] = list(by_rid.get(r["roster_id"], []))
    storage.save_rosters(DISCO.league_id, rosters)
    return rosters


def test_a_pick_still_on_the_roster_is_held(storage):
    _drafted(storage)
    aging = draft_aging(storage, DISCO)
    assert all(r["status"] == "held" for rows in aging.values() for r in rows)
    assert team_summary(aging[1])["gone"] == 0


def test_a_cut_pick_reads_as_gone_before_a_game_is_played(storage):
    """Two of the three questions are answerable in August, which is when
    'your biggest reach is already off your roster' is funniest."""
    rosters = _drafted(storage)
    dropped = rosters[0]["players"].pop()
    storage.save_rosters(DISCO.league_id, rosters)
    aging = draft_aging(storage, DISCO)
    row = next(r for r in aging[rosters[0]["roster_id"]] if r["player_id"] == dropped)
    assert row["status"] == "gone"
    assert aging_line(row) == "No longer on any roster in the league."


def test_a_traded_pick_is_distinguished_from_a_cut_one(storage):
    rosters = _drafted(storage)
    moved = rosters[0]["players"].pop()
    rosters[1]["players"].append(moved)
    storage.save_rosters(DISCO.league_id, rosters)
    aging = draft_aging(storage, DISCO)
    row = next(r for r in aging[rosters[0]["roster_id"]] if r["player_id"] == moved)
    assert row["status"] == "traded"
    assert row["now_roster_id"] == rosters[1]["roster_id"]


def test_usage_waits_for_a_real_sample(storage):
    """A start count off one week says nothing about a draft pick."""
    _drafted(storage)
    populate_matchups(storage, DISCO, week=1, teams=12,
                      scores={rid: 100.0 + rid for rid in range(1, 13)},
                      players_points={1: {"p1": 12.0}}, starters={1: ["p1"]})
    rows = draft_aging(storage, DISCO)[1]
    assert all("starts" not in r for r in rows)


def test_usage_is_reported_once_there_is_one(storage):
    _drafted(storage)
    for wk in (1, 2, 3):
        populate_matchups(storage, DISCO, week=wk, teams=12,
                          scores={rid: 100.0 + rid for rid in range(1, 13)},
                          players_points={1: {"p1": 12.0}}, starters={1: ["p1"]})
    row = next(r for r in draft_aging(storage, DISCO)[1] if r["player_id"] == "p1")
    assert row["starts"] == 3
    assert row["points"] == 36.0
    assert "started 3 of 3 weeks for 36 points" in aging_line(row)


def test_the_market_call_is_quoted_never_recomputed(storage):
    """REACH and STEAL are the comparison made on the night. Re-scoring one
    later with the benefit of results would be rewriting history to win an
    argument."""
    rosters = _drafted(storage)
    gone = rosters[0]["players"][0]
    rosters[0]["players"] = rosters[0]["players"][1:]
    storage.save_rosters(DISCO.league_id, rosters)
    rows = draft_aging(storage, DISCO)[rosters[0]["roster_id"]]
    name = next(r["name"] for r in rows if r["player_id"] == gone)
    out = departed_headliners(rows, {name: "REACH"})
    assert len(out) == 1
    assert out[0]["label"] == "REACH"
    # a player still held never appears here, whatever he was called
    held = next(r["name"] for r in rows if r["status"] == "held")
    assert departed_headliners(rows, {held: "STEAL"}) == []
