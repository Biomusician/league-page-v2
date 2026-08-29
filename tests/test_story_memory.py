from __future__ import annotations

from leaguepage.matchup_angles import generate_angles
from leaguepage.story_memory import retrieve_callbacks, story_memory_for_matchup

from fixtures import TEST_LEAGUE, populate_league, populate_matchups


def _seed_archive(storage):
    storage.upsert_archive_issue(
        league_slug="disco", season="2021", week=4, title="2021 Disco Week 4",
        source_path="archive/disco/x.md",
        body="McLovin once again demonstrated why Anomalies fear no one.",
        dating_confidence="high",
    )
    storage.upsert_archive_issue(
        league_slug="surfeit", season="2026", week=None, title="Surfeit Draft Issue",
        source_path="archive/surfeit/y.md",
        body="Team One arrived with an embarrassment of running backs.",
        dating_confidence="high",
    )
    storage.upsert_archive_issue(
        league_slug="surfeit", season="2025", week=3, title="Old Surfeit Issue",
        source_path="archive/surfeit/z.md",
        body="Team One collapsed in spectacular fashion that week.",
        dating_confidence="medium",
    )


TEAM = {"team_slug": "team-1", "team_name": "Team One", "roster_id": 1,
        "manager_keys": ["m1"], "manager_display_names": ["Manager1"]}

MANAGERS_NO_CROSS = {"m1": {"aliases": ["McLovin"], "leagues": {}}}
MANAGERS_CROSS_OK = {"m1": {"aliases": ["McLovin"], "allow_cross_league_callbacks": True, "leagues": {}}}


def test_cross_league_callbacks_suppressed_by_default(storage):
    _seed_archive(storage)
    hits = retrieve_callbacks(storage, "surfeit", [TEAM], MANAGERS_NO_CROSS, season="2026")
    # the disco McLovin hit must NOT appear; the surfeit team-name hits do
    assert all(h["source_league"] == "surfeit" for h in hits)
    assert hits, "same-league hits should still be found"


def test_cross_league_callbacks_allowed_when_explicitly_marked(storage):
    _seed_archive(storage)
    hits = retrieve_callbacks(storage, "surfeit", [TEAM], MANAGERS_CROSS_OK, season="2026")
    cross = [h for h in hits if h["source_league"] == "disco"]
    assert cross and cross[0]["cross_league"] is True


def test_one_strong_callback_and_date_reliability(storage):
    _seed_archive(storage)
    hits = retrieve_callbacks(storage, "surfeit", [TEAM], MANAGERS_NO_CROSS, season="2026")
    strong = [h for h in hits if h["strength"] == "strong"]
    assert len(strong) == 1
    assert strong[0]["date_unreliable"] is False
    # the medium-confidence issue is flagged
    flagged = [h for h in hits if h["title"] == "Old Surfeit Issue"]
    assert not flagged or flagged[0]["date_unreliable"] is True


def test_reuse_pushes_callback_down(storage):
    _seed_archive(storage)
    first = retrieve_callbacks(storage, "surfeit", [TEAM], MANAGERS_NO_CROSS, season="2026")
    top = first[0]
    storage.log_editorial_usage(league_slug="surfeit", season="2026", week=1,
                                kind="callback", value=top["evidence"])
    second = retrieve_callbacks(storage, "surfeit", [TEAM], MANAGERS_NO_CROSS, season="2026")
    assert second[0]["evidence"] != top["evidence"] or second[0]["prior_reuse"] > 0
    reused = next(h for h in second if h["evidence"] == top["evidence"])
    assert reused["prior_reuse"] == 1 and reused["strength"] != "strong"


def test_repeated_joke_lane_warning(storage):
    populate_league(storage, teams=10, rounds=3, picks="complete")
    populate_matchups(storage, week=2, teams=10)
    from leaguepage.matchup_analysis import analyze_week

    storage.log_editorial_usage(league_slug="testleague", season="2026", week=1,
                                kind="frame", value="wildcard", note="Oregon Trail bit")
    a = analyze_week(storage, TEST_LEAGUE, 2)
    m = a["matchups"][0]
    sm = story_memory_for_matchup(storage, "testleague", "2026", m, {})
    angles = generate_angles(storage, m, sm)
    wildcard = next(x for x in angles if x["family"] == "wildcard")
    assert any("Oregon Trail bit" in w for w in wildcard["collision_warnings"])


def test_coalition_lane_rotation(storage):
    populate_league(storage, teams=10, rounds=3, picks="complete")
    populate_matchups(storage, week=2, teams=10)
    from leaguepage.matchup_analysis import analyze_week

    coalitions = {
        "identities": {}, "relationships": [],
        "coalitions": [{"key": "fra-uk", "name": "FRA/UK", "members": [], "tags": ["Rafale"],
                        "roster_mapping": {"league": "testleague", "roster_id": 1,
                                           "status": "confirmed"}}],
    }
    storage.log_editorial_usage(league_slug="testleague", season="2026", week=1,
                                kind="joke_family", value="coalition-command-relationships")
    a = analyze_week(storage, TEST_LEAGUE, 2)
    m = next(x for x in a["matchups"] if x["teams"][0]["roster_id"] == 1)
    sm = story_memory_for_matchup(storage, "testleague", "2026", m, {})
    angles = generate_angles(storage, m, sm, coalitions=coalitions)
    coalition_angle = next(x for x in angles if x["angle_id"].endswith(":coalition"))
    # rotates off the used lane to the next fresh one
    assert "fighter-culture" in coalition_angle["premise"]
    assert any("coalition-command-relationships" in w for w in coalition_angle["collision_warnings"])


def test_angle_families_are_distinct(storage):
    populate_league(storage, teams=10, rounds=3, picks="complete")
    populate_matchups(storage, week=1, teams=10)
    from leaguepage.matchup_analysis import analyze_week

    a = analyze_week(storage, TEST_LEAGUE, 1)
    m = a["matchups"][0]
    sm = story_memory_for_matchup(storage, "testleague", "2026", m, {})
    angles = generate_angles(storage, m, sm)
    families = [x["family"] for x in angles]
    assert len(families) == len(set(families)), "no duplicate families"
    assert 3 <= len(angles) <= 5
    for x in angles:
        assert x["strength"] in ("strong", "medium", "speculative")
        assert x["evidence"]
