from __future__ import annotations

from leaguepage.matchup_analysis import analyze_week
from leaguepage.matchup_interest import (
    classify, competitive_importance, recommend_prominence, story_value,
)

from fixtures import TEST_LEAGUE, populate_league, populate_matchups, set_records

CONFIRMED_COALITIONS = {
    "identities": {},
    "coalitions": [
        {"key": "fra-uk", "name": "FRA/UK", "members": ["FRA", "UK"], "tags": ["Rafale"],
         "roster_mapping": {"league": "testleague", "roster_id": 1, "status": "confirmed"}},
        {"key": "jpn-swe", "name": "JPN/SWE", "members": ["JPN", "SWE"], "tags": ["Gripen"],
         "roster_mapping": {"league": "testleague", "roster_id": 2, "status": "confirmed"}},
    ],
    "relationships": [
        {"between": ["fra-uk", "jpn-swe"], "type": "Coalition Rivalry", "status": "confirmed"},
    ],
}

INFERRED_COALITIONS = {
    "identities": {}, "relationships": [],
    "coalitions": [
        {"key": "fra-uk", "name": "FRA/UK", "members": [], "tags": [],
         "roster_mapping": {"league": "testleague", "roster_id": 1, "status": "inferred"}},
    ],
}


def _week(storage, records=None, teams=10):
    populate_league(storage, teams=teams, rounds=3, picks="complete")
    populate_matchups(storage, week=4, teams=teams)
    # mark 3 played weeks so records/standings are meaningful
    for wk in range(1, 4):
        populate_matchups(storage, week=wk, teams=teams,
                          scores={rid: 100.0 + rid for rid in range(1, teams + 1)})
    if records:
        set_records(storage, records=records)
    return analyze_week(storage, TEST_LEAGUE, 4)


def test_top_table_and_basement_components(storage):
    records = {rid: (3, 0, 400) if rid in (1, 2) else ((0, 3, 200) if rid in (9, 10) else (1, 2, 300))
               for rid in range(1, 11)}
    a = _week(storage, records=records)
    # matchup (1,2) = top table; (9,10) = basement
    m_top = next(m for m in a["matchups"] if m["teams"][0]["roster_id"] == 1)
    m_bot = next(m for m in a["matchups"] if m["teams"][0]["roster_id"] == 9)
    ci_top = competitive_importance(m_top, a)
    ci_bot = competitive_importance(m_bot, a)
    assert any("top table" in c["label"] for c in ci_top["components"])
    assert any("basement" in c["label"] for c in ci_bot["components"])
    assert ci_top["score"] > ci_bot["score"]


def test_score_is_sum_of_components_capped(storage):
    a = _week(storage)
    m = a["matchups"][0]
    ci = competitive_importance(m, a)
    assert ci["score"] == min(100, sum(c["points"] for c in ci["components"]))


def test_confirmed_coalition_boosts_story_value_and_tags(storage):
    a = _week(storage)
    m = next(x for x in a["matchups"] if x["teams"][0]["roster_id"] == 1)  # 1 vs 2
    sv = story_value(m, coalitions=CONFIRMED_COALITIONS)
    labels = [c["label"] for c in sv["components"]]
    assert any("coalition" in l for l in labels)
    assert any("rivalry" in l for l in labels)
    tags = classify(m, competitive_importance(m, a), sv, a)
    assert "Coalition Warfare" in tags and "Rivalry" in tags


def test_inferred_coalition_gives_no_boost(storage):
    a = _week(storage)
    m = next(x for x in a["matchups"] if x["teams"][0]["roster_id"] == 1)
    sv = story_value(m, coalitions=INFERRED_COALITIONS)
    assert not any("coalition" in c["label"] for c in sv["components"])


def test_coalition_not_forced_to_feature(storage):
    """Coalition boosts Story Value but a stronger combined matchup can still
    out-rank it for FEATURE."""
    a = _week(storage)
    scored = []
    for m in a["matchups"]:
        ci = competitive_importance(m, a)
        sv = story_value(m, coalitions=CONFIRMED_COALITIONS,
                         commissioner_flagged=(m["teams"][0]["roster_id"] == 5))
        scored.append({"matchup": m, "competitive_importance": ci, "story_value": sv})
    # rig a non-coalition matchup with overwhelming competitive + flag
    for s in scored:
        if s["matchup"]["teams"][0]["roster_id"] == 5:
            s["competitive_importance"] = {"score": 100, "components": []}
            s["story_value"] = {"score": 100, "components": []}
    recommend_prominence(scored)
    feature = next(s for s in scored if s["recommended_prominence"] == "FEATURE")
    assert feature["matchup"]["teams"][0]["roster_id"] == 5


def test_prominence_distribution_scales_with_league_size(storage):
    a = _week(storage, teams=12)
    scored = [{"matchup": m,
               "competitive_importance": competitive_importance(m, a),
               "story_value": story_value(m)} for m in a["matchups"]]
    recommend_prominence(scored)
    levels = [s["recommended_prominence"] for s in scored]
    assert levels.count("FEATURE") == 1
    assert levels.count("MAJOR") == 2
    assert len(levels) == 6  # 12-team league: six matchups, no crash
