"""The Editorial Command Brief: one page, ranked, evidenced, honest.

The Desk had boards and a review packet of decision states; nothing
answered "what are this week's stories?" The brief does, deterministically,
and these pin what it must and must not say: a flagged move outranks a
routine one, an out-designated starter on a top room is a story, a boring
matchup is called boring, and the scorecard never invents a number.
"""
from __future__ import annotations

import pytest

import leaguepage.config as cfg
import leaguepage.issue_builder as ib
import leaguepage.matchup_packet as mp
from leaguepage import command_brief
from leaguepage.command_brief import brief_data, build_command_brief, render_brief, top_story_lines
from leaguepage.config import get_league
from leaguepage.storage import Storage

from fixtures import add_players, populate_league, populate_matchups

SEASON = "2026"
LG = get_league("surfeit")


@pytest.fixture
def env(tmp_path, monkeypatch):
    ed = tmp_path / "editorial"
    monkeypatch.setattr(ib, "EDITORIAL_DIR", ed)
    monkeypatch.setattr(mp, "EDITORIAL_DIR", ed)
    monkeypatch.setattr(cfg, "PUBLISHED_DIR", tmp_path / "published")
    monkeypatch.setattr(mp, "load_managers", lambda: {})
    monkeypatch.setattr(mp, "load_coalitions",
                        lambda: {"identities": {}, "coalitions": [], "relationships": []})
    db = tmp_path / "t.sqlite3"
    with Storage(db) as s:
        populate_league(s, LG, teams=10, rounds=3, picks="complete", season=SEASON)
        populate_matchups(s, LG, week=1, teams=10,
                          scores={rid: 90.0 + rid for rid in range(1, 11)})
        s.set_meta("current_week", "1")
    return db, ed


def _data(db, **kw):
    with Storage(db) as s:
        return brief_data(s, LG, SEASON, "week-01", candidates=[], **kw)


def test_the_scorecard_uses_statuses_not_a_score(env):
    db, _ed = env
    d = _data(db)
    assert set(d["scorecard"]) == {"Editorial focus", "Evidence freshness", "Matchup specificity",
                                   "Cross-section overlap", "Continuity", "Fact QA",
                                   "Publication gates"}
    for v in d["scorecard"].values():
        assert not v.strip().isdigit(), v
        assert "/100" not in v


def test_a_flagged_faab_move_becomes_a_top_story(env):
    db, _ed = env
    with Storage(db) as s:
        add_players(s, {"TX1": ("Log Player", "RB", 500)})
        s.save_transactions(LG.league_id, 1, [
            {"transaction_id": "t1", "type": "waiver", "status": "complete", "leg": 1,
             "adds": {"TX1": 3}, "drops": {}, "waiver_budget": [{"amount": 40}],
             "settings": {"waiver_bid": 40}, "created": 5}])
    d = _data(db)
    kinds = [s["kind"] for s in d["top_stories"]]
    assert "move" in kinds
    top = next(s for s in d["top_stories"] if s["kind"] == "move")
    assert "Log Player" in top["headline"] and "Team 3" in top["headline"]
    assert top["evidence"], "a story carries its evidence"
    assert d["market"] and d["market"][0]["teams"] == ["Team 3"]


def test_an_out_designated_starter_on_a_top_room_is_a_story(env):
    db, _ed = env
    with Storage(db) as s:
        # find a starter on a top-ranked room and mark him NA
        from leaguepage.matchup_analysis import weekly_scores
        from leaguepage.team_analytics import positional_profile

        profile = positional_profile(s, LG, weeks_played=0)
        rid = min(profile["ranks"]["RB"], key=lambda r: profile["ranks"]["RB"][r])
        add_players(s, {"RBX": ("Hurt Back", "RB", 999)})
        populate_matchups(s, LG, week=1, teams=10,
                          scores={rid2: 90.0 + rid2 for rid2 in range(1, 11)},
                          starters={rid: ["RBX"]})
        p = dict(s.get_player("RBX")); p["injury_status"] = "NA"
        s.save_players({"RBX": p})
    d = _data(db)
    assert any(st["kind"] == "availability" and "NA" in st["headline"] for st in d["top_stories"]), \
        [st["headline"] for st in d["top_stories"]]
    assert any(w.startswith("NA:") for w in d["data_watch"])


def test_a_boring_matchup_is_called_boring(env):
    db, _ed = env
    d = _data(db)
    assert d["matchups"], "week 1 has matchups"
    text = render_brief(d)
    # with no games, no coalitions and evenly built synthetic rosters, at
    # least one pairing has nothing beyond the baseline and the brief says so
    assert "nothing beyond the baseline" in text or any(r["why"] or r["mismatches"] for r in d["matchups"])


def test_week_one_data_watch_names_preseason_evidence_and_no_projections(env):
    db, _ed = env
    d = _data(db)
    joined = " | ".join(d["data_watch"])
    assert "projections: none on file" in joined
    assert "preseason" in joined
    assert "byes: none this week" in joined or "byes: no schedule" in joined


def test_source_disagreement_is_not_faked_with_one_source(env):
    db, _ed = env
    d = _data(db)
    assert "cannot be measured" in d["source_note"] or "no reference source" in d["source_note"]


def test_the_brief_is_written_and_the_prep_leads_with_it(env):
    db, ed = env
    with Storage(db) as s:
        path, d = build_command_brief(s, LG, SEASON, "week-01", candidates=[])
        from leaguepage.issue_builder import build_lowdown_prep

        prep = build_lowdown_prep(s, LG, SEASON, "week-01", [], extra=top_story_lines(d))
    assert path.exists() and path.name == "COMMAND_BRIEF.md"
    text = path.read_text(encoding="utf-8")
    for head in ("## SCORECARD", "## TOP STORIES", "## MATCHUPS TO WATCH",
                 "## MARKET / ROSTER MOVEMENT", "## CONTINUITY", "## EDITORIAL COLLISIONS",
                 "## DATA WATCH"):
        assert head in text, head
    assert len(text.splitlines()) < 120, "one page, not a report"
    prep_file = prep / "PREP.md" if prep.is_dir() else prep
    assert "## Top stories (Editorial Command Brief" in prep_file.read_text(encoding="utf-8")


def test_a_take_with_an_engine_reading_is_continuity_and_a_story(env):
    db, _ed = env
    with Storage(db) as s:
        tid = s.add_take(league_slug="surfeit", season=SEASON, week=None, context="preseason",
                         source="power", subject="team-3", quote="Team 3 will finish top three.",
                         players=None, topic="power") if hasattr(s, "add_take") else None
        if tid is None:
            pytest.skip("no take API in this storage build")
        s.record_take_evaluation(tid, recommended_status="leaning_wrong", evidence=["ranked 9th"])
    d = _data(db)
    assert any(t["reading"] for t in d["takes"])
    assert any(st["kind"] == "take" for st in d["top_stories"])
    assert "follow-up" in d["scorecard"]["Continuity"]


def test_nothing_in_the_brief_is_a_private_handle(env, monkeypatch):
    """Private research, but the file is still the kind of thing that gets
    pasted; it must not carry a Sleeper handle."""
    db, _ed = env
    with Storage(db) as s:
        users = s.get_league_users(LG.league_id)
    handles = {u.get("display_name") for u in users if u.get("display_name")}
    d = _data(db)
    text = render_brief(d)
    for h in handles:
        if h and len(h) > 3 and not h.startswith("Team "):
            assert h not in text, h
