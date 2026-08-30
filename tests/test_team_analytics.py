from __future__ import annotations

import pytest

from leaguepage.config import League
from leaguepage.storage import Storage
from leaguepage.team_analytics import (
    analytics_story_candidates, get_snapshot, label_for_rank, league_positions,
    playoff_outlook, positional_profile, recent_form, record_snapshot,
    roster_contrast_lines, scoring_streaks, snapshot_deltas, strengths_weaknesses,
)
from leaguepage.team_names import identity_rows, resolve_public_names, sleeper_team_names

from fixtures import add_players, populate_league, populate_matchups

LG = League(slug="tl", display_name="TL", league_id="TID", theme="disco",
            subtitle="t", adp_source="")


class FakeADP:
    """rank lookup by (name, position)."""

    def __init__(self, ranks):
        self.ranks = ranks

    def lookup(self, name, position=None):
        return self.ranks.get(name)


def _league(storage, *, teams=4, superflex=False, kdst=False):
    populate_league(storage, LG, teams=teams, rounds=1)
    data = storage.get_league(LG.league_id)
    slots = ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX"]
    if superflex:
        slots.append("SUPER_FLEX")
    if kdst:
        slots += ["K", "DEF"]
    data["roster_positions"] = slots + ["BN", "BN", "BN"]
    storage.save_league(LG.league_id, data)
    return data


def _rosters_with_players(storage, spec: dict[int, list[str]]):
    """spec: roster_id -> player ids; player registry via add_players."""
    rosters = []
    for rid, pids in spec.items():
        rosters.append({"roster_id": rid, "owner_id": f"u{rid}", "players": pids})
    storage.save_rosters(LG.league_id, rosters)


@pytest.fixture
def db(tmp_path):
    with Storage(tmp_path / "t.sqlite3") as s:
        yield s


# --------------------------------------------------------- team identity

def test_sleeper_name_resolution_and_precedence(db):
    populate_league(db, LG, teams=4)
    assert sleeper_team_names(db, LG)[1] == "Team 1"
    db.set_public_team_name(LG.slug, 1, "Custom Name")
    r = resolve_public_names(db, LG)
    assert r[1] == {"name": "Custom Name", "source": "commissioner"}   # override wins
    assert r[2] == {"name": "Team 2", "source": "sleeper-team-name"}


def test_neutral_placeholder_yields_to_sleeper_name(db):
    populate_league(db, LG, teams=4)
    db.set_public_team_name(LG.slug, 3, "Roster 3")   # neutral MVP placeholder
    assert resolve_public_names(db, LG)[3] == {"name": "Team 3",
                                               "source": "sleeper-team-name"}


def test_handles_never_become_public_names(db):
    populate_league(db, LG, teams=2)
    users = db.get_league_users(LG.league_id)
    for u in users:
        u["metadata"] = {}          # no team names anywhere
    db.save_league_users(LG.league_id, users)
    r = resolve_public_names(db, LG)
    assert all(v["name"] is None for v in r.values())   # never Manager1 etc.


def test_identity_rows_context_and_rename_detection(db):
    populate_league(db, LG, teams=4, co_managed_roster=2)
    db.set_public_team_name(LG.slug, 1, "Old Override")
    rows = {r["roster_id"]: r for r in identity_rows(db, LG)}
    assert rows[1]["renamed_on_sleeper"] is True        # override != "Team 1"
    assert rows[2]["co_managed"] and len(rows[2]["owners"]) == 2
    assert rows[3]["sleeper_name"] == "Team 3"
    assert rows[1]["draft_slot"] == 1                   # round-1 slot resolved


# ----------------------------------------------------- positional engine

def _basic_world(db, *, superflex=False, teams=2):
    _league(db, teams=teams, superflex=superflex)
    ranks = {}
    spec = {}
    pid = 0
    for rid in range(1, teams + 1):
        pids = []
        for pos, count, base in (("QB", 2, 10), ("RB", 4, 20), ("WR", 4, 30),
                                 ("TE", 2, 60)):
            for i in range(count):
                pid += 1
                name = f"P{pid}"
                pids.append(str(pid))
                # roster 1 strictly better than roster 2 etc.
                ranks[name] = base + i * 12 + (rid - 1) * 6
        spec[rid] = pids
    add_players(db, {str(i): (f"P{i}", p, 1) for i, p in _positions_of(spec, db)})
    _rosters_with_players(db, spec)
    return FakeADP(ranks)


def _positions_of(spec, db):
    # rebuild position mapping matching _basic_world's generation order
    out = []
    pid = 0
    for rid in spec:
        for pos, count in (("QB", 2), ("RB", 4), ("WR", 4), ("TE", 2)):
            for _ in range(count):
                pid += 1
                out.append((pid, pos))
    return out


def test_league_positions_respect_lineup_settings(db):
    data = _league(db, kdst=True)
    assert "K" in league_positions(data) and "DEF" in league_positions(data)
    data2 = _league(db, kdst=False)
    assert "K" not in league_positions(data2)


def test_superflex_increases_qb_demand(db):
    adp = _basic_world(db, superflex=True)
    p_sf = positional_profile(db, LG, adp=adp)
    with Storage(db.db_path.parent / "b.sqlite3") as db2:
        adp2 = _basic_world(db2, superflex=False)
        p_1qb = positional_profile(db2, LG, adp=adp2)
    rid = 1
    assert (p_sf["teams"][rid]["QB"]["starters_used"]
            > p_1qb["teams"][rid]["QB"]["starters_used"])


def test_starters_vs_depth_distinction(db):
    _league(db, teams=2)
    # both teams get identical WRs so the FLEX slot never blurs the RB rooms:
    # team 1 has elite RB starters and no depth; team 2 the reverse shape.
    ranks = {"A1": 1, "A2": 2, "A3": 250, "A4": 260,
             "B1": 40, "B2": 45, "B3": 60, "B4": 70,
             "W1a": 20, "W1b": 25, "W1c": 30, "W2a": 20, "W2b": 25, "W2c": 30,
             "T1": 15, "T2": 15, "Q1": 5, "Q2": 6}
    reg = {p: (p, "RB", 1) for p in ("A1", "A2", "A3", "A4", "B1", "B2", "B3", "B4")}
    reg |= {p: (p, "WR", 1) for p in ("W1a", "W1b", "W1c", "W2a", "W2b", "W2c")}
    reg |= {"T1": ("T1", "TE", 1), "T2": ("T2", "TE", 1),
            "Q1": ("Q1", "QB", 1), "Q2": ("Q2", "QB", 1)}
    add_players(db, reg)
    # each side has a symmetric spare WR (rank 30) that the FLEX slot takes,
    # so the RB rooms compare purely on their dedicated starters + bench
    _rosters_with_players(db, {1: ["A1", "A2", "A3", "A4", "Q1", "T1", "W1a", "W1b", "W1c"],
                               2: ["B1", "B2", "B3", "B4", "Q2", "T2", "W2a", "W2b", "W2c"]})
    p = positional_profile(db, LG, adp=FakeADP(ranks))
    assert p["starter_ranks"]["RB"][1] == 1        # elite dedicated starters
    assert p["depth_ranks"]["RB"][1] == 2          # nothing behind them
    assert p["depth_ranks"]["RB"][2] == 1          # the deep room wins depth


def test_top_heavy_room_flagged_fragile(db):
    _league(db, teams=2)
    ranks = {"Star": 1, "Scrub1": 240, "Scrub2": 245,
             "Even1": 50, "Even2": 55, "Even3": 60}
    add_players(db, {p: (p, "WR", 1) for p in ranks})
    _rosters_with_players(db, {1: ["Star", "Scrub1", "Scrub2"],
                               2: ["Even1", "Even2", "Even3"]})
    p = positional_profile(db, LG, adp=FakeADP(ranks))
    assert p["teams"][1]["WR"]["fragility"] > 0.9
    assert p["teams"][2]["WR"]["fragility"] < 0.5


def test_unranked_players_are_replacement_level(db):
    _league(db, teams=2)
    add_players(db, {"X": ("X", "RB", 1), "Y": ("Y", "RB", 1)})
    _rosters_with_players(db, {1: ["X"], 2: ["Y"]})
    p = positional_profile(db, LG, adp=FakeADP({"X": 10}))   # Y unranked
    assert p["teams"][2]["RB"]["room_score"] == 0.0
    assert p["ranks"]["RB"][1] == 1


def test_ten_and_twelve_team_rank_ranges(db):
    for n, path in ((10, "ten.sqlite3"), (12, "twelve.sqlite3")):
        with Storage(db.db_path.parent / path) as s:
            _league(s, teams=n)
            ranks, spec = {}, {}
            for rid in range(1, n + 1):
                nm = f"T{rid}"
                ranks[nm] = rid * 5
                spec[rid] = [nm]
            add_players(s, {nm: (nm, "QB", 1) for nm in ranks})
            _rosters_with_players(s, spec)
            p = positional_profile(s, LG, adp=FakeADP(ranks))
            assert sorted(p["ranks"]["QB"].values()) == list(range(1, n + 1))
            assert label_for_rank(1, n) == "Strength"
            assert label_for_rank(n, n) == "Major Weakness"


def test_methodology_stage_transition(db):
    adp = _basic_world(db)
    p0 = positional_profile(db, LG, adp=adp, weeks_played=0)
    assert "preseason" in p0["stage"]
    for wk in (1, 2, 3):
        populate_matchups(db, LG, week=wk, teams=2,
                          scores={1: 100.0, 2: 90.0},
                          players_points={1: {"1": 20.0}, 2: {"17": 5.0}})
    p3 = positional_profile(db, LG, adp=adp, weeks_played=3)
    assert p3["stage"] == "in-season blend"


# --------------------------------------------------- form/streaks/playoffs

def _play_weeks(db, results: dict[int, list[float]]):
    teams = len(results)
    for wk in range(1, len(next(iter(results.values()))) + 1):
        populate_matchups(db, LG, week=wk, teams=teams,
                          scores={rid: results[rid][wk - 1] for rid in results})


def test_recent_form_and_streaks(db):
    _league(db, teams=4)
    _rosters_with_players(db, {i: [] for i in range(1, 5)})
    _play_weeks(db, {1: [100, 110, 120, 130], 2: [90, 80, 70, 60],
                     3: [100, 100, 100, 100], 4: [95, 96, 97, 98]})
    form = recent_form(db, LG, 4)
    assert form[1]["rank"] == 1 and form[2]["rank"] == 4
    st = scoring_streaks(db, LG, 4)
    assert st[1]["kind"] == "top-half scoring" and st[1]["length"] >= 3
    assert st[2]["kind"] == "bottom-half scoring"


def test_playoff_outlook_early_suppression_and_simulation(db):
    _league(db, teams=4)
    populate_league(db, LG, teams=4)
    data = db.get_league(LG.league_id)
    data["settings"] = {"playoff_teams": 2, "playoff_week_start": 8}
    db.save_league(LG.league_id, data)
    _rosters_with_players(db, {i: [] for i in range(1, 5)})
    _play_weeks(db, {1: [120, 121], 2: [80, 82], 3: [110, 108], 4: [90, 95]})
    early = playoff_outlook(db, LG, 1)
    assert early["stage"] == "too_early"          # only 2 played weeks
    _play_weeks(db, {1: [120, 121, 125, 130], 2: [80, 82, 79, 85],
                     3: [110, 108, 112, 111], 4: [90, 95, 92, 96]})
    mid = playoff_outlook(db, LG, 4, sims=400)
    assert mid["stage"] == "bands"
    assert mid["teams"][1]["odds"] > mid["teams"][2]["odds"]   # dominant team favored
    assert 0.0 <= mid["teams"][2]["odds"] <= 1.0
    # deterministic: same inputs, same odds (seeded)
    assert playoff_outlook(db, LG, 4, sims=400)["teams"][1]["odds"] == mid["teams"][1]["odds"]


def test_snapshots_persist_and_deltas_are_historical(db):
    import json

    adp = _basic_world(db, teams=2)
    record_snapshot(db, LG, "2026", 0, adp=adp)
    assert get_snapshot(db, LG, "2026", 0)["stage"].startswith("preseason")
    # deltas compare stored history, never recomputed: plant two snapshots
    db.set_meta("analytics_snapshot:tl:2026:0", json.dumps({
        "week": 0, "stage": "preseason",
        "positional_ranks": {"WR": {"1": 9, "2": 1}},
        "standings": {"1": 6, "2": 1}, "playoff": {"1": 0.10, "2": 0.80}}))
    db.set_meta("analytics_snapshot:tl:2026:2", json.dumps({
        "week": 2, "stage": "preseason",
        "positional_ranks": {"WR": {"1": 3, "2": 1}},
        "standings": {"1": 2, "2": 1}, "playoff": {"1": 0.45, "2": 0.80}}))
    deltas = snapshot_deltas(db, LG, "2026", 2)
    assert any("WR room #9 → #3" in n for n in deltas[1])
    assert any(n.startswith("standings 6 → 2") for n in deltas[1])
    assert any("playoff odds" in n for n in deltas[1])
    assert 2 not in deltas                     # nothing changed for team 2


def test_analytics_story_candidates_gated_and_shaped(db):
    _league(db, teams=4)
    _rosters_with_players(db, {i: [] for i in range(1, 5)})
    assert analytics_story_candidates(db, LG, "2026", 1, {}) == []   # no games
    _play_weeks(db, {1: [120, 125, 130, 128], 2: [80, 82, 79, 78],
                     3: [110, 90, 112, 84], 4: [95, 118, 92, 121]})
    # wins diverge from all-play for team 3 style patterns; at minimum streaks fire
    cands = analytics_story_candidates(db, LG, "2026", 4,
                                       {i: f"Team {i}" for i in range(1, 5)})
    assert any(c["candidate_id"].startswith("analytics:streak") for c in cands)
    for c in cands:
        assert c["headline"] and c["recommended_sections"]


def test_roster_contrast_lines(db):
    adp = _basic_world(db, teams=2)
    p = positional_profile(db, LG, adp=adp)
    lines = roster_contrast_lines(p, 1, 2, "Alpha", "Beta")
    assert any("best room" in ln for ln in lines)
