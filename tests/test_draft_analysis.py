from __future__ import annotations

from leaguepage.draft_analysis import analyze_league_draft
from leaguepage.draft_awards import draft_award_nominations
from leaguepage.draft_stories import draft_story_candidates

from fixtures import TEST_LEAGUE, make_adp, player_name, populate_league


def test_complete_ten_team_draft(storage):
    populate_league(storage, teams=10, rounds=3, picks="complete")
    a = analyze_league_draft(storage, TEST_LEAGUE)
    assert a["draft_status"] == "complete"
    assert a["pick_count"] == 30 and a["expected_pick_count"] == 30
    assert len(a["teams"]) == 10
    assert all(t["pick_count"] == 3 for t in a["teams"])
    # snake: roster 1 gets picks 1, 20, 21
    t1 = next(t for t in a["teams"] if t["roster_id"] == 1)
    assert [p["pick_no"] for p in t1["picks_by_round"]] == [1, 20, 21]


def test_twelve_team_format_not_hardcoded(storage):
    populate_league(storage, teams=12, rounds=4, picks="complete")
    a = analyze_league_draft(storage, TEST_LEAGUE)
    assert a["total_teams"] == 12
    assert a["pick_count"] == 48
    assert len(a["teams"]) == 12


def test_incomplete_draft_warns_but_analyzes(storage):
    populate_league(storage, teams=10, rounds=4, picks="partial")
    a = analyze_league_draft(storage, TEST_LEAGUE)
    assert a["pick_count"] == 20
    assert any("analysis reflects 20 picks" in w for w in a["warnings"])


def test_empty_draft_graceful(storage):
    populate_league(storage, teams=10, rounds=3, picks="none")
    a = analyze_league_draft(storage, TEST_LEAGUE)
    assert a["pick_count"] == 0
    assert a["teams"] and all(t["pick_count"] == 0 for t in a["teams"])
    assert draft_story_candidates(a) == []
    assert draft_award_nominations(a) == []


def test_no_draft_at_all_returns_none(storage):
    storage.save_league(TEST_LEAGUE.league_id, {"league_id": TEST_LEAGUE.league_id, "season": "2026"})
    assert analyze_league_draft(storage, TEST_LEAGUE) is None


def test_co_managed_team_identity(storage):
    populate_league(storage, teams=10, rounds=3, picks="complete", co_managed_roster=7)
    a = analyze_league_draft(storage, TEST_LEAGUE)
    t7 = next(t for t in a["teams"] if t["roster_id"] == 7)
    assert t7["co_managed"] is True
    assert set(t7["manager_display_names"]) == {"Manager7", "CoManager7"}


def test_missing_adp_source(storage):
    populate_league(storage, teams=10, rounds=3, picks="complete")
    a = analyze_league_draft(storage, TEST_LEAGUE, adp=None)
    assert all(p["delta"] is None for p in a["picks"])
    assert any("deltas unavailable" in w for w in a["warnings"])
    # nominations that need deltas disappear rather than fabricate
    awards = {aw["award_key"] for aw in draft_award_nominations(a)}
    assert "best-value" not in awards and "biggest-reach" not in awards


def test_reach_value_deltas_exact(storage):
    populate_league(storage, teams=10, rounds=3, picks="complete")
    # pick 2 was ranked 20 (reach of 18); pick 15 was ranked 4 (value of 11);
    # pick 5's player missing from the source entirely
    adp = make_adp({2: 20.0, 15: 4.0, 5: None})
    a = analyze_league_draft(storage, TEST_LEAGUE, adp=adp)
    by_no = {p["pick_no"]: p for p in a["picks"]}
    assert by_no[2]["delta"] == -18.0 and by_no[2]["adp"] == 20.0
    assert by_no[15]["delta"] == 11.0
    assert by_no[2]["adp_source"] == "test_ref"
    # unmatched player: no delta, reported in warnings, never fabricated
    assert by_no[5]["delta"] is None and by_no[5]["adp"] is None
    assert player_name(5) in a["unmatched_adp_players"]
    assert any("no entry" in w for w in a["warnings"])
    # league-wide extremes found them
    assert a["league_biggest_reaches"][0]["pick_no"] == 2
    assert a["league_biggest_values"][0]["pick_no"] == 15


def test_every_pick_and_candidate_has_evidence(storage):
    populate_league(storage, teams=10, rounds=3, picks="complete")
    adp = make_adp({2: 20.0})
    a = analyze_league_draft(storage, TEST_LEAGUE, adp=adp)
    assert all(p["evidence"] for p in a["picks"])
    for c in draft_story_candidates(a):
        assert c["evidence"], f"candidate without evidence: {c['candidate_id']}"
    for aw in draft_award_nominations(a):
        for n in aw["nominees"]:
            assert n["evidence"], f"nominee without evidence in {aw['award_key']}"


def test_candidate_ids_stable_across_reruns(storage):
    populate_league(storage, teams=10, rounds=3, picks="complete")
    adp = make_adp({2: 20.0, 15: 4.0})
    a1 = analyze_league_draft(storage, TEST_LEAGUE, adp=adp)
    a2 = analyze_league_draft(storage, TEST_LEAGUE, adp=adp)
    ids1 = [c["candidate_id"] for c in draft_story_candidates(a1)]
    ids2 = [c["candidate_id"] for c in draft_story_candidates(a2)]
    assert ids1 == ids2
