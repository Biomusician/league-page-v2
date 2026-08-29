from __future__ import annotations

import json

from leaguepage.adp import ADPSource
from leaguepage.dossier import write_dossiers
from leaguepage.draft_analysis import analyze_league_draft
from leaguepage.draft_awards import draft_award_nominations
from leaguepage.draft_stories import draft_story_candidates
from leaguepage.editorial import load_managers

from fixtures import TEST_LEAGUE, make_adp, populate_league


def _analysis(storage, **kwargs):
    populate_league(storage, teams=10, rounds=3, picks="complete", **kwargs)
    return analyze_league_draft(storage, TEST_LEAGUE, adp=make_adp({2: 20.0, 15: 4.0}))


def test_dossiers_written_and_factual(storage, tmp_path):
    a = _analysis(storage)
    cands = draft_story_candidates(a)
    awards = draft_award_nominations(a)
    paths = write_dossiers(a, cands, awards, {}, base_dir=tmp_path)
    assert len(paths) == 11  # 10 teams + league
    # pick #2 (the crafted reach) belongs to roster 2 — its dossier carries the
    # pick-level evidence via the reach candidate
    team_doc = (tmp_path / "2026" / "testleague" / "draft" / "dossiers" / "team-2.md").read_text(encoding="utf-8")
    assert "## Evidence" in team_doc
    assert "sleeper:pick:D-TEST123:2" in team_doc
    assert "Test Reference Ranks" in team_doc
    league_doc = paths[-1].read_text(encoding="utf-8")
    assert "League Draft Dossier" in league_doc


def test_packet_generation_and_idempotency(storage, tmp_path, monkeypatch):
    import leaguepage.packet as packet_mod

    populate_league(storage, teams=10, rounds=3, picks="complete")
    # patch the loaders so the packet uses synthetic ADP and empty editorial data
    monkeypatch.setattr(packet_mod, "load_adp_for_league", lambda lg: make_adp({2: 20.0}))
    monkeypatch.setattr(packet_mod, "load_managers", lambda: {})
    monkeypatch.setattr(packet_mod, "load_coalitions", lambda: {"identities": {}, "coalitions": [], "relationships": []})

    out1 = packet_mod.build_draft_packet(storage, TEST_LEAGUE, base_dir=tmp_path)
    assert out1 is not None
    files = {p.relative_to(out1).as_posix() for p in out1.rglob("*") if p.is_file()}
    expected = {"AUTHORING_BRIEF.md", "MANIFEST.json", "data.json", "analytics.json",
                "story_candidates.md", "award_nominations.md", "commissioner_decisions.md",
                "archive_context.md", "manager_context.md", "takes.md"}
    assert expected <= files
    assert any(f.startswith("team_dossiers/") for f in files)

    snapshot = {
        f: (out1 / f).read_text(encoding="utf-8")
        for f in files if f != "MANIFEST.json"
    }
    out2 = packet_mod.build_draft_packet(storage, TEST_LEAGUE, base_dir=tmp_path)
    for f, text in snapshot.items():
        assert (out2 / f).read_text(encoding="utf-8") == text, f"{f} not idempotent"

    manifest = json.loads((out1 / "MANIFEST.json").read_text(encoding="utf-8"))
    assert manifest["adp_source"] == "test_ref"


def test_packet_reflects_commissioner_decisions(storage, tmp_path, monkeypatch):
    import leaguepage.packet as packet_mod

    populate_league(storage, teams=10, rounds=3, picks="complete")
    monkeypatch.setattr(packet_mod, "load_adp_for_league", lambda lg: make_adp({2: 20.0}))
    monkeypatch.setattr(packet_mod, "load_managers", lambda: {})
    monkeypatch.setattr(packet_mod, "load_coalitions", lambda: {"identities": {}, "coalitions": [], "relationships": []})

    a = analyze_league_draft(storage, TEST_LEAGUE, adp=make_adp({2: 20.0}))
    cid = draft_story_candidates(a)[0]["candidate_id"]
    storage.set_story_decision(league_slug="testleague", season="2026", workflow="draft",
                               candidate_id=cid, decision="include", note="lead with this")
    storage.set_award_decision(league_slug="testleague", season="2026", workflow="draft",
                               award_key="biggest-reach", decision="awarded", winner="team-2")
    storage.save_power_rankings("testleague", "2026", "preseason",
                                [{"roster_id": 1, "rank": 1, "tier": 1, "note": "the favorite"}])
    storage.add_take(league_slug="testleague", season="2026", week=None, context="draft",
                     source="draft-review", subject="team-1", quote="Best roster on paper.")

    out = packet_mod.build_draft_packet(storage, TEST_LEAGUE, base_dir=tmp_path)
    stories = (out / "story_candidates.md").read_text(encoding="utf-8")
    assert "INCLUDED" in stories and "lead with this" in stories
    awards_md = (out / "award_nominations.md").read_text(encoding="utf-8")
    assert "AWARDED — team-2" in awards_md
    decisions = (out / "commissioner_decisions.md").read_text(encoding="utf-8")
    assert "#1 Team 1" in decisions and "the favorite" in decisions
    takes = (out / "takes.md").read_text(encoding="utf-8")
    assert "Best roster on paper." in takes


def test_packet_manager_context_bans_unverified(storage, tmp_path, monkeypatch):
    import leaguepage.packet as packet_mod

    populate_league(storage, teams=10, rounds=3, picks="complete", co_managed_roster=3)
    fake_managers = {
        "manager3": {
            "sleeper_user_id": "u3", "display_name": "Manager3",
            "aliases": ["The Verified One"],
            "unverified_aliases": [{"name": "SneakyAlias", "status": "inferred",
                                    "evidence": "a hunch", "rule": "do not use"}],
            "identity": {"nationality": "", "role": "", "notes": ""},
            "leagues": {"testleague": {"roster_id": 3}},
        }
    }
    monkeypatch.setattr(packet_mod, "load_adp_for_league", lambda lg: None)
    monkeypatch.setattr(packet_mod, "load_managers", lambda: fake_managers)
    monkeypatch.setattr(packet_mod, "load_coalitions", lambda: {"identities": {}, "coalitions": [], "relationships": []})

    out = packet_mod.build_draft_packet(storage, TEST_LEAGUE, base_dir=tmp_path)
    ctx = (out / "manager_context.md").read_text(encoding="utf-8")
    confirmed_part, banned_part = ctx.split("## BANNED")
    assert "The Verified One" in confirmed_part
    assert "SneakyAlias" not in confirmed_part
    assert "SneakyAlias" in banned_part


def test_real_editorial_files_load_into_packet_shapes():
    # regression: repo editorial files stay compatible with the packet code paths
    managers = load_managers()
    assert all(isinstance(m.get("aliases", []), list) for m in managers.values())


def test_adp_source_lookup_edges():
    src = ADPSource(source_key="t", source_name="T", kind="test", scoring_format="",
                    retrieved_at="", note="",
                    players=[{"name": "D.J. Example", "position": "WR", "team": "AAA", "rank": 7},
                             {"name": "Sam Same", "position": "RB", "team": "BBB", "rank": 12},
                             {"name": "Sam Same", "position": "WR", "team": "CCC", "rank": 90}])
    assert src.lookup("DJ Example", "WR") == 7          # punctuation-insensitive
    assert src.lookup("Sam Same", "WR") == 90           # position-qualified
    assert src.lookup("Sam Same") == 12                 # name-only: best rank wins
    assert src.lookup("Nobody Here") is None
