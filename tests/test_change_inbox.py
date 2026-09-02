"""Tier 1 acceptance: Change Inbox detection, Story Significance ranking, and
the red-team cases that decide whether the inbox is usable or noise.

Ranking behaviour is tested against hand-built snapshot payloads rather than a
live league, so the assertions are deterministic and do not depend on what any
real fantasy team did this week. One end-to-end test covers the wiring.
"""
from __future__ import annotations

import pytest

import leaguepage.issue_builder as ib
import leaguepage.matchup_packet as mp
from leaguepage import change_inbox as ci
from leaguepage import significance as sig
from leaguepage.config import get_league
from leaguepage.storage import Storage

from fixtures import populate_league, populate_matchups, set_records

SEASON = "2026"
N = 10
NAMES = {i: f"Team {i}" for i in range(1, N + 1)}


# --------------------------------------------------------------- payloads

def _payload(**over) -> dict:
    """A complete, boring league state. Tests move one thing at a time."""
    base = {
        "week": 5, "weeks_played": 5, "n_teams": N,
        "standings": {str(i): i for i in range(1, N + 1)},
        "records": {str(i): [5, 0] for i in range(1, N + 1)},
        "points_for": {str(i): 500.0 for i in range(1, N + 1)},
        "all_play": {str(i): [30, 15] for i in range(1, N + 1)},
        "playoff": {str(i): 0.50 for i in range(1, N + 1)},
        "playoff_spots": 6,
        "positional_ranks": {"RB": {str(i): i for i in range(1, N + 1)},
                             "WR": {str(i): i for i in range(1, N + 1)}},
        "streaks": {},
        "results": {},
        "transactions": [],
        "high_score": 150.0, "low_score": 60.0,
    }
    base.update(over)
    return base


def _diff(before_over: dict, after_over: dict) -> list[dict]:
    return ci.diff_snapshots(_payload(**before_over), _payload(**after_over),
                             NAMES, league_id="L1")


def _by_id(items: list[dict], prefix: str) -> dict | None:
    return next((i for i in items if i["item_id"].startswith(prefix)), None)


def _scored(items):
    return sig.rank(items)


# ------------------------------------------------------- change detection

def test_upset_is_detected_with_before_and_after():
    items = _diff({}, {"results": {"5:1": [9, 130.0, 2, 100.0]}})
    up = _by_id(items, "change:upset")
    assert up is not None
    assert "Team 9" in up["headline"] and "Team 2" in up["headline"]
    assert up["before"] == "Team 9 9th, Team 2 2nd"
    assert up["after"] == "Team 9 130, Team 2 100"
    assert up["magnitude"] == pytest.approx(7 / 9)


def test_a_favourite_winning_is_not_an_upset():
    items = _diff({}, {"results": {"5:1": [2, 130.0, 9, 100.0]}})
    assert _by_id(items, "change:upset") is None


def test_blowout_and_photo_finish_are_separate_items():
    blow = _diff({}, {"results": {"5:1": [1, 150.0, 2, 100.0]}})
    assert _by_id(blow, "change:blowout") is not None
    assert _by_id(blow, "change:narrow") is None
    close = _diff({}, {"results": {"5:1": [1, 101.0, 2, 100.0]}})
    assert _by_id(close, "change:narrow") is not None
    assert _by_id(close, "change:blowout") is None


def test_standings_movement_carries_before_and_after():
    items = _diff({}, {"standings": {**{str(i): i for i in range(1, N + 1)},
                                     "8": 3, "3": 8}})
    mv = _by_id(items, "change:standings:8")
    assert mv["before"] == "8th" and mv["after"] == "3rd"
    assert "5 places" in mv["magnitude_label"]


def test_new_leader_and_new_cellar_are_their_own_items():
    after = {str(i): i for i in range(1, N + 1)}
    after["4"], after["1"] = 1, 4
    items = _diff({}, {"standings": after})
    lead = _by_id(items, "change:leader")
    assert lead and lead["before"] == "Team 1" and lead["after"] == "Team 4"


def test_playoff_swing_clinch_and_elimination():
    swing = _diff({}, {"playoff": {**{str(i): 0.50 for i in range(1, N + 1)}, "3": 0.85}})
    it = _by_id(swing, "change:playoff:3")
    assert it["before"] == "50%" and it["after"] == "85%"

    clinch = _diff({}, {"playoff": {**{str(i): 0.50 for i in range(1, N + 1)}, "3": 1.0}})
    assert _by_id(clinch, "change:playoff-clinch:3") is not None
    elim = _diff({}, {"playoff": {**{str(i): 0.50 for i in range(1, N + 1)}, "3": 0.0}})
    assert _by_id(elim, "change:playoff-elim:3") is not None


def test_positional_strength_shift():
    after = {"RB": {**{str(i): i for i in range(1, N + 1)}, "7": 2, "2": 7},
             "WR": {str(i): i for i in range(1, N + 1)}}
    items = _diff({}, {"positional_ranks": after})
    it = _by_id(items, "change:pos:RB:7")
    assert it["before"] == "#7" and it["after"] == "#2"
    assert "strengthened" in it["headline"]


def test_new_season_high_is_a_record_item():
    items = _diff({}, {"high_score": 191.0, "results": {"5:1": [4, 191.0, 5, 100.0]}})
    rec = _by_id(items, "change:season-high")
    assert rec and rec["before"] == "150" and rec["after"] == "191"
    assert rec["rarity"] == 0.8


def test_only_new_results_are_reported():
    before = {"results": {"4:1": [1, 120.0, 2, 100.0]}}
    after = {"results": {"4:1": [1, 120.0, 2, 100.0],
                         "5:1": [9, 130.0, 2, 100.0]}}
    ids = [i["item_id"] for i in _diff(before, after)]
    assert any("5:1" in i for i in ids)
    assert not any("4:1" in i for i in ids)


# ------------------------------------------------- materiality floors (noise)

def test_one_place_standings_move_never_becomes_an_item():
    after = {str(i): i for i in range(1, N + 1)}
    after["5"], after["6"] = 6, 5
    assert _by_id(_diff({}, {"standings": after}), "change:standings") is None


def test_one_percent_playoff_drift_never_becomes_an_item():
    after = {str(i): 0.50 for i in range(1, N + 1)}
    after["3"] = 0.51
    assert _by_id(_diff({}, {"playoff": after}), "change:playoff") is None


def test_two_place_positional_wobble_never_becomes_an_item():
    after = {"RB": {**{str(i): i for i in range(1, N + 1)}, "5": 3, "3": 5},
             "WR": {str(i): i for i in range(1, N + 1)}}
    assert _by_id(_diff({}, {"positional_ranks": after}), "change:pos") is None


def test_an_unplayed_week_produces_nothing():
    assert _diff({}, {}) == []


# --------------------------------------------------- significance red team

def _item(item_id, category, magnitude, **kw):
    return {"item_id": item_id, "category": category, "magnitude": magnitude,
            "teams": kw.pop("teams", []), "evidence": [], **kw}


TRIVIA = [
    _item("change:standings:5", "standings", 1 / 9, consequence=0.1),
    _item("change:txn:k-swap", "transaction", 0.05, cost=0.02),
    _item("change:playoff:5", "playoff", 0.02, consequence=0.06),
    _item("change:pos:RB:5", "strength", 0.11, consequence=0.15),
]

MAJOR = [
    _item("change:upset:5:1", "result", 0.9, consequence=0.5, expectation=0.9),
    _item("change:playoff-elim:7", "playoff", 1.0, consequence=1.0, rarity=0.9),
    _item("change:season-high:191", "record", 1.0, rarity=0.8, history=0.5),
    _item("change:txn:blockbuster", "transaction", 0.9, cost=0.9),
    _item("change:receipt:12", "receipt", 0.55, receipt=0.9, history=0.4),
]


def test_every_major_item_outranks_every_trivial_one():
    ranked = _scored(TRIVIA + MAJOR)
    scores = {i["item_id"]: i["significance"]["score"] for i in ranked}
    worst_major = min(scores[i["item_id"]] for i in MAJOR)
    best_trivia = max(scores[i["item_id"]] for i in TRIVIA)
    assert worst_major > best_trivia, scores


def test_trivia_lands_in_the_minor_band():
    for i in _scored(TRIVIA):
        assert i["significance"]["band"] == "Minor", i["item_id"]


def test_elimination_and_a_record_reach_the_top_band():
    ranked = _scored(MAJOR)
    top = {i["item_id"] for i in ranked if i["significance"]["band"] in ("Lead story", "Strong")}
    assert "change:playoff-elim:7" in top
    assert "change:season-high:191" in top


def test_a_trade_is_not_automatically_important():
    """The roadmap's explicit red-team case: no category is hardcoded."""
    small = _item("change:txn:small", "transaction", 0.16, cost=0.16)
    elim = _item("change:playoff-elim:7", "playoff", 1.0, consequence=1.0, rarity=0.9)
    ranked = _scored([small, elim])
    assert ranked[0]["item_id"] == "change:playoff-elim:7"
    assert ranked[1]["significance"]["band"] == "Minor"


def test_a_big_trade_does_outrank_a_modest_standings_move():
    big = _item("change:txn:big", "transaction", 0.9, cost=0.85, consequence=0.4)
    modest = _item("change:standings:4", "standings", 0.25, consequence=0.3)
    assert _scored([modest, big])[0]["item_id"] == "change:txn:big"


def test_repetition_penalty_demotes_a_lane_that_just_ran():
    item = _item("change:standings:4", "standings", 0.6, consequence=0.5)
    fresh = sig.score_item(item)
    stale = sig.score_item(item, sig.repetition_context(item, {"change:standings": 1}))
    assert stale["score"] < fresh["score"] - 20
    assert any("ran last week" in c["label"] for c in stale["components"])


def test_repetition_penalty_decays_and_expires():
    item = _item("change:standings:4", "standings", 0.6, consequence=0.5)
    scores = [sig.score_item(item, sig.repetition_context(item, {"change:standings": w}))["score"]
              for w in (1, 2, 3, 9)]
    assert scores == sorted(scores)          # colder lane, higher score
    assert scores[-1] == sig.score_item(item)["score"]   # fully expired


def test_every_item_explains_itself():
    for i in _scored(TRIVIA + MAJOR):
        lines = sig.explain(i)
        assert lines, i["item_id"]
        assert all(line[0] in "+-" for line in lines)


def test_explanation_names_the_penalty_that_demoted_an_item():
    item = _item("change:pos:RB:5", "strength", 0.11)
    lines = sig.explain({**item, "significance": sig.score_item(item)})
    assert any("below the materiality floor" in line for line in lines)


def test_ranking_is_stable_across_runs():
    a = [i["item_id"] for i in _scored(TRIVIA + MAJOR)]
    b = [i["item_id"] for i in _scored(list(reversed(TRIVIA + MAJOR)))]
    assert a == b


# --------------------------------------------------------------- snapshots

def test_a_sync_that_changed_nothing_does_not_move_the_baseline(storage):
    p = _payload()
    assert storage.record_sync_snapshot(league_slug="disco", season=SEASON, week=5, payload=p)
    assert storage.record_sync_snapshot(league_slug="disco", season=SEASON, week=5, payload=p) is None
    assert len(storage.list_sync_snapshots("disco", SEASON)) == 1


def test_baseline_is_the_previous_material_state(storage):
    storage.record_sync_snapshot(league_slug="disco", season=SEASON, week=4, payload=_payload(week=4))
    storage.record_sync_snapshot(league_slug="disco", season=SEASON, week=5, payload=_payload(week=5))
    assert storage.baseline_sync_snapshot("disco", SEASON)["payload"]["week"] == 4


def test_marking_reviewed_pins_the_baseline_forward(storage):
    for wk in (3, 4, 5):
        storage.record_sync_snapshot(league_slug="disco", season=SEASON, week=wk,
                                     payload=_payload(week=wk))
    storage.mark_sync_reviewed("disco", SEASON)          # pins week 5
    storage.record_sync_snapshot(league_slug="disco", season=SEASON, week=6,
                                 payload=_payload(week=6))
    assert storage.baseline_sync_snapshot("disco", SEASON)["payload"]["week"] == 5


def test_same_second_syncs_do_not_overwrite_each_other(storage):
    stamp = "2026-10-06T12:00:00+00:00"
    storage.record_sync_snapshot(league_slug="disco", season=SEASON, week=4,
                                 payload=_payload(week=4), taken_at=stamp)
    storage.record_sync_snapshot(league_slug="disco", season=SEASON, week=5,
                                 payload=_payload(week=5), taken_at=stamp)
    assert len(storage.list_sync_snapshots("disco", SEASON)) == 2


# ------------------------------------------------------------- end to end

@pytest.fixture
def league_env(tmp_path, monkeypatch):
    monkeypatch.setattr(ib, "EDITORIAL_DIR", tmp_path / "editorial")
    monkeypatch.setattr(mp, "EDITORIAL_DIR", tmp_path / "editorial")
    monkeypatch.setattr(mp, "load_managers", lambda: {})
    monkeypatch.setattr(mp, "load_coalitions",
                        lambda: {"identities": {}, "coalitions": [], "relationships": []})
    league = get_league("surfeit")
    db = tmp_path / "t.sqlite3"
    with Storage(db) as s:
        populate_league(s, league, teams=N, rounds=3, picks="complete", season=SEASON)
        s.set_meta("current_week", "2")
        # BEFORE: week 1 played, chalk result
        populate_matchups(s, league, week=1, teams=N,
                          scores={rid: 120.0 - rid for rid in range(1, N + 1)})
        set_records(s, league, {rid: (1, 0, 120.0 - rid) if rid % 2 else (0, 1, 120.0 - rid)
                                for rid in range(1, N + 1)})
    return db, league, tmp_path


def test_inbox_end_to_end_finds_the_new_week(league_env):
    db, league, tmp = league_env
    with Storage(db) as s:
        first = ci.record(s, league, SEASON, 1)
        assert first is not None
        board = ci.build_inbox(s, league, SEASON)
        assert board["has_baseline"] is False   # one snapshot is not a comparison

        # AFTER: week 2 played, the bottom seed wins big
        populate_matchups(s, league, week=2, teams=N,
                          scores={**{rid: 100.0 for rid in range(1, N + 1)},
                                  9: 175.0, 10: 95.0, 1: 96.0, 2: 150.0})
        set_records(s, league, {9: (2, 0, 295.0), 1: (1, 1, 215.0),
                                2: (1, 1, 268.0)})
        s.set_meta("current_week", "2")
        assert ci.record(s, league, SEASON, 2) is not None

        board = ci.build_inbox(s, league, SEASON)

    assert board["has_baseline"] is True
    ids = [i["item_id"] for i in board["items"]]
    assert any(i.startswith("change:") for i in ids), ids
    assert board["counts"]["total"] >= 1
    for it in board["items"]:
        assert it["significance"]["score"] >= 0
        assert it["why"]                      # every item explains itself
        assert it["sections"]                 # every item suggests a destination
    scores = [i["significance"]["score"] for i in board["items"]]
    assert scores == sorted(scores, reverse=True)


def test_decisions_are_stored_where_the_issue_builder_already_reads_them(league_env):
    db, league, tmp = league_env
    with Storage(db) as s:
        ci.record(s, league, SEASON, 1)
        populate_matchups(s, league, week=2, teams=N,
                          scores={**{rid: 100.0 for rid in range(1, N + 1)}, 9: 175.0})
        ci.record(s, league, SEASON, 2)
        board = ci.build_inbox(s, league, SEASON)
        item = board["items"][0]
        s.set_story_decision(league_slug=league.slug, season=SEASON,
                             workflow=board["issue_key"], candidate_id=item["item_id"],
                             decision="include", route="lowdown")
        again = ci.build_inbox(s, league, SEASON)
        stored = next(i for i in again["items"] if i["item_id"] == item["item_id"])
        assert stored["decision"] == "include" and stored["route"] == "lowdown"
        # the same row the authoring briefs read
        assert s.get_story_decisions(league.slug, SEASON, board["issue_key"])[
            item["item_id"]]["route"] == "lowdown"


def test_inbox_never_touches_commissioner_prose(league_env):
    db, league, tmp = league_env
    idir = tmp / "editorial" / SEASON / league.slug / "week-02" / "sections"
    idir.mkdir(parents=True, exist_ok=True)
    prose = idir / "tracks.md"
    prose.write_text("Commissioner words that must survive.\n", encoding="utf-8")
    with Storage(db) as s:
        ci.record(s, league, SEASON, 1)
        populate_matchups(s, league, week=2, teams=N,
                          scores={**{rid: 100.0 for rid in range(1, N + 1)}, 9: 175.0})
        ci.record(s, league, SEASON, 2)
        ci.build_inbox(s, league, SEASON)
    assert prose.read_text(encoding="utf-8") == "Commissioner words that must survive.\n"


# ------------------------------------------- Tier 1 #2: postgame auto-refresh

def test_phase_flips_from_preview_to_result_when_points_arrive():
    unplayed = {"teams": [{"team_slug": "a", "points": None},
                          {"team_slug": "b", "points": None}]}
    zeroes = {"teams": [{"team_slug": "a", "points": 0.0},
                        {"team_slug": "b", "points": 0.0}]}
    played = {"teams": [{"team_slug": "a", "points": 118.4},
                        {"team_slug": "b", "points": 96.2}]}
    assert mp.phase_of(unplayed) == "preview"
    assert mp.phase_of(zeroes) == "preview"     # a synced-but-unplayed week
    assert mp.phase_of(played) == "result"


def test_result_block_states_the_score_and_forbids_invented_causation():
    played = {"teams": [{"team_slug": "a", "team_name": "Alpha", "points": 118.4},
                        {"team_slug": "b", "team_name": "Bravo", "points": 96.2}]}
    block = mp._result_block(played)
    assert "Alpha 118.4" in block and "Bravo 96.2" in block
    assert "margin 22.2" in block
    assert "Do not claim causation" in block
    assert "RESULT" in block


def test_refresh_rewrites_briefs_and_never_touches_commissioner_prose(league_env):
    """The issue is one artefact that evolves. Research is regenerated on every
    sync; the commissioner's own words are not in scope for regeneration."""
    from leaguepage.desk import refresh_issue_research

    db, league, tmp = league_env
    idir = tmp / "editorial" / SEASON / league.slug / "week-02"
    (idir / "sections").mkdir(parents=True, exist_ok=True)
    (idir / "lowdown").mkdir(parents=True, exist_ok=True)
    prose = {
        idir / "sections" / "tracks.md": "Tracks prose the commissioner wrote.\n",
        idir / "lowdown" / "lowdown.md": "The Lowdown, in his voice.\n",
    }
    for path, text in prose.items():
        path.write_text(text, encoding="utf-8")

    with Storage(db) as s:
        populate_matchups(s, league, week=2, teams=N,
                          scores={rid: 100.0 + rid for rid in range(1, N + 1)})
        s.set_meta("current_week", "2")
        refresh_issue_research(s, league, SEASON, "week-02")

    for path, text in prose.items():
        assert path.read_text(encoding="utf-8") == text, path

    briefs = list((idir / "matchups").rglob("generated/AUTHORING.md"))
    assert briefs, "refresh should have produced matchup briefs"
    body = briefs[0].read_text(encoding="utf-8")
    assert "<!-- phase: result -->" in body
    assert "RESULT (this matchup has been played)" in body
    assert "Do not claim causation" in body


def test_a_preview_week_brief_asks_for_a_preview(league_env):
    from leaguepage.desk import refresh_issue_research

    db, league, tmp = league_env
    with Storage(db) as s:
        populate_matchups(s, league, week=2, teams=N)     # synced, unplayed
        s.set_meta("current_week", "2")
        refresh_issue_research(s, league, SEASON, "week-02")
    idir = tmp / "editorial" / SEASON / league.slug / "week-02"
    briefs = list((idir / "matchups").rglob("generated/AUTHORING.md"))
    assert briefs
    body = briefs[0].read_text(encoding="utf-8")
    assert "<!-- phase: preview -->" in body
    assert "RESULT (this matchup has been played)" not in body


def test_standings_moves_are_suppressed_against_a_preseason_baseline():
    """Preseason order is an arbitrary tiebreak among 0-0 teams. Diffing week 1
    against it reported all twelve teams as movers and buried the real story."""
    after = {str(i): i for i in range(1, N + 1)}
    after["12" if N >= 12 else "9"], after["1"] = 1, 9
    items = ci.diff_snapshots(_payload(weeks_played=0), _payload(weeks_played=1, standings=after),
                              NAMES, league_id="L1")
    assert _by_id(items, "change:standings") is None
    assert _by_id(items, "change:leader") is None


def test_standings_moves_survive_once_both_sides_have_games():
    after = {str(i): i for i in range(1, N + 1)}
    after["8"], after["3"] = 3, 8
    items = ci.diff_snapshots(_payload(weeks_played=2), _payload(weeks_played=3, standings=after),
                              NAMES, league_id="L1")
    assert _by_id(items, "change:standings:8") is not None


# --------------------------------------------------- the Desk surface itself

@pytest.fixture
def desk(league_env):
    """The real app on the synthetic league, with one played week to triage."""
    from fastapi.testclient import TestClient

    from leaguepage.desk import create_app

    db, league, tmp = league_env
    with Storage(db) as s:
        ci.record(s, league, SEASON, 1)
        populate_matchups(s, league, week=2, teams=N,
                          scores={**{rid: 100.0 for rid in range(1, N + 1)},
                                  9: 175.0, 10: 95.0})
        set_records(s, league, {9: (2, 0, 295.0)})
        s.set_meta("current_week", "2")
        ci.record(s, league, SEASON, 2)
    return TestClient(create_app(db)), league, db


def test_inbox_page_renders_with_actions_and_explanations(desk):
    client, league, _ = desk
    r = client.get(f"/commissioner/inbox?league={league.slug}")
    assert r.status_code == 200
    for probe in ("Change Inbox", "Add to Issue", "Ignore This Week",
                  "Save for Later", "Why this surfaced", "Show evidence",
                  "Mark all reviewed"):
        assert probe in r.text, probe


def test_inbox_page_is_usable_on_a_phone(desk):
    """Tier 1 triage has to work from a phone, so this is a real requirement."""
    client, league, _ = desk
    body = client.get(f"/commissioner/inbox?league={league.slug}").text
    assert "width=device-width" in body
    assert "@media (max-width: 40rem)" in body


def test_inbox_decision_round_trips_through_the_page(desk):
    client, league, db = desk
    r = client.post(f"/commissioner/{league.slug}/{SEASON}/inbox/decide",
                    data={"item_id": "change:test:1", "decision": "include",
                          "route": "lowdown", "issue_key": "week-02",
                          "note": "open with this"},
                    follow_redirects=False)
    assert r.status_code == 303
    with Storage(db) as s:
        d = s.get_story_decisions(league.slug, SEASON, "week-02")["change:test:1"]
    assert d["decision"] == "include" and d["route"] == "lowdown"
    assert d["note"] == "open with this"


def test_ignore_this_week_is_scoped_to_that_week(desk):
    """Ignoring is per-issue, so a lane suppressed in week 2 is offered again
    in week 3 rather than disappearing for the season."""
    client, league, db = desk
    client.post(f"/commissioner/{league.slug}/{SEASON}/inbox/decide",
                data={"item_id": "change:test:1", "decision": "ignore",
                      "route": "", "issue_key": "week-02", "note": ""},
                follow_redirects=False)
    with Storage(db) as s:
        assert "change:test:1" in s.get_story_decisions(league.slug, SEASON, "week-02")
        assert "change:test:1" not in s.get_story_decisions(league.slug, SEASON, "week-03")


def test_mark_reviewed_pins_the_baseline_from_the_page(desk):
    client, league, db = desk
    r = client.post(f"/commissioner/{league.slug}/{SEASON}/inbox/reviewed",
                    follow_redirects=False)
    assert r.status_code == 303
    with Storage(db) as s:
        assert s.latest_sync_snapshot(league.slug, SEASON)["reviewed_at"]
    assert client.get(f"/commissioner/inbox?league={league.slug}").status_code == 200


def test_inbox_renders_for_a_league_with_no_data_at_all(desk):
    """The other league in the registry has never been synced here."""
    client, league, _ = desk
    r = client.get("/commissioner/inbox")
    assert r.status_code == 200
