from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import leaguepage.publish as publish_mod
from leaguepage.config import get_league
from leaguepage.desk import create_app
from leaguepage.matchup_packet import ROUGH_DRAFT_MARKER, build_weekly_packet
from leaguepage.publish import render_week
from leaguepage.storage import Storage

from fixtures import TEST_LEAGUE, populate_league, populate_matchups


@pytest.fixture
def week_env(storage, tmp_path, monkeypatch):
    import leaguepage.matchup_packet as mp

    monkeypatch.setattr(mp, "load_managers", lambda: {})
    monkeypatch.setattr(mp, "load_coalitions",
                        lambda: {"identities": {}, "coalitions": [], "relationships": []})
    populate_league(storage, teams=10, rounds=3, picks="complete")
    populate_matchups(storage, week=1, teams=10)
    return storage, tmp_path


def test_packet_layout_and_authoring_brief(week_env):
    storage, tmp = week_env
    root = build_weekly_packet(storage, TEST_LEAGUE, 1, base_dir=tmp)
    assert root is not None
    mdirs = sorted((root / "matchups").iterdir())
    assert len(mdirs) == 5
    gen = mdirs[0] / "generated"
    for f in ("data.json", "analytics.json", "history.md", "story_memory.md",
              "angles.md", "evidence.json", "AUTHORING.md"):
        assert (gen / f).exists(), f
    authoring = (gen / "AUTHORING.md").read_text(encoding="utf-8")
    # the authoritative skill path is required reading
    assert ".claude/skills/my-writing-style/SKILL.md" in authoring
    assert "UNVERIFIED / DO NOT ASSERT" in authoring
    assert ROUGH_DRAFT_MARKER in authoring
    analytics = json.loads((gen / "analytics.json").read_text(encoding="utf-8"))
    assert "not objective" in analytics["score_disclaimer"]


def test_packet_idempotent_and_preserves_commissioner_files(week_env):
    storage, tmp = week_env
    root = build_weekly_packet(storage, TEST_LEAGUE, 1, base_dir=tmp)
    slug_dir = sorted((root / "matchups").iterdir())[0]
    notes = slug_dir / "commissioner_notes.md"
    notes.write_text("# Notes\n\nRoast the bench.\n", encoding="utf-8")
    draft = slug_dir / "draft.md"
    draft.write_text("my precious draft\n", encoding="utf-8")
    snapshot = {p.relative_to(root).as_posix(): p.read_text(encoding="utf-8")
                for p in root.rglob("*") if p.is_file() and "week.json" not in p.name
                and "AUTHORING" not in p.name}
    build_weekly_packet(storage, TEST_LEAGUE, 1, base_dir=tmp)
    for rel, text in snapshot.items():
        assert (root / rel).read_text(encoding="utf-8") == text, rel
    # notes flowed into the rebuilt AUTHORING
    authoring = (slug_dir / "generated" / "AUTHORING.md").read_text(encoding="utf-8")
    assert "Roast the bench." in authoring


def test_selected_angle_and_revisions_flow_into_authoring(week_env):
    storage, tmp = week_env
    root = build_weekly_packet(storage, TEST_LEAGUE, 1, base_dir=tmp)
    slug = sorted(p.name for p in (root / "matchups").iterdir())[0]
    storage.set_matchup_state(league_slug="testleague", season="2026", week=1,
                              matchup_slug=slug, selected_angle_id=f"{slug}:competitive",
                              revision_requests=["shorter", "new opening: colder"],
                              status="ready_to_draft")
    build_weekly_packet(storage, TEST_LEAGUE, 1, base_dir=tmp)
    authoring = (root / "matchups" / slug / "generated" / "AUTHORING.md").read_text(encoding="utf-8")
    assert f"SELECTED" in authoring and f"{slug}:competitive" in authoring
    assert "- shorter" in authoring and "new opening: colder" in authoring


def test_public_week_refuses_unapproved_prose(week_env, monkeypatch):
    storage, tmp = week_env
    monkeypatch.setattr(publish_mod, "SITE_DIR", tmp / "site")
    root = build_weekly_packet(storage, TEST_LEAGUE, 1, base_dir=tmp)
    slug = sorted(p.name for p in (root / "matchups").iterdir())[0]
    draft = root / "matchups" / slug / "draft.md"
    draft.write_text(f"<!-- {ROUGH_DRAFT_MARKER} -->\nUnapproved rough prose.\n", encoding="utf-8")
    out = render_week(storage, TEST_LEAGUE, 1, editorial_dir=tmp)
    html = out.read_text(encoding="utf-8")
    assert "Unapproved rough prose" not in html
    assert "Preview pending" in html

    # approve a clean draft -> it renders; others stay pending
    draft.write_text("A clean approved preview about Team 1.\n", encoding="utf-8")
    storage.set_matchup_state(league_slug="testleague", season="2026", week=1,
                              matchup_slug=slug, status="approved")
    html = render_week(storage, TEST_LEAGUE, 1, editorial_dir=tmp).read_text(encoding="utf-8")
    assert "A clean approved preview" in html
    assert html.count("Preview pending") == 4


def test_desk_matchup_decisions_persist(tmp_path):
    db = tmp_path / "desk.sqlite3"
    with Storage(db) as s:
        populate_league(s, get_league("surfeit"), teams=10, rounds=3, picks="complete")
        populate_matchups(s, get_league("surfeit"), week=1, teams=10)
    client = TestClient(create_app(db))
    r = client.get("/commissioner/surfeit/2026/week/1/matchups")
    assert r.status_code == 200
    # find a slug from the page
    slug = "team-1-vs-team-2"
    r = client.post(f"/commissioner/surfeit/2026/week/1/matchups/{slug}/angle",
                    data={"angle_id": f"{slug}:competitive", "action": "select", "note": ""},
                    follow_redirects=False)
    assert r.status_code == 303
    r = client.post(f"/commissioner/surfeit/2026/week/1/matchups/{slug}/prominence",
                    data={"prominence": "CAPSULE"})
    r = client.post(f"/commissioner/surfeit/2026/week/1/matchups/{slug}/angle",
                    data={"action": "note", "note": "watch the TE room", "angle_id": ""})
    with Storage(db) as s:
        state = s.get_matchup_state("surfeit", "2026", 1, slug)
    assert state["selected_angle_id"] == f"{slug}:competitive"
    assert state["prominence_override"] == "CAPSULE"
    assert state["angle_note"] == "watch the TE room"
    assert state["status"] == "ready_to_draft"


def test_desk_draft_lifecycle_marker_blocks_approval(tmp_path, monkeypatch):
    import leaguepage.desk as desk_mod

    db = tmp_path / "desk.sqlite3"
    with Storage(db) as s:
        populate_league(s, get_league("surfeit"), teams=10, rounds=3, picks="complete")
        populate_matchups(s, get_league("surfeit"), week=1, teams=10)
    # sandbox the editorial dir the desk writes drafts into
    monkeypatch.setattr(desk_mod, "week_dir",
                        lambda league, season, week, base_dir=None:
                        tmp_path / "editorial" / season / league.slug / f"week-{week:02d}")
    client = TestClient(create_app(db))
    slug = "team-1-vs-team-2"
    rough = f"<!-- {ROUGH_DRAFT_MARKER} -->\nDraft text."
    client.post(f"/commissioner/surfeit/2026/week/1/matchups/{slug}/draft",
                data={"draft_text": rough})
    client.post(f"/commissioner/surfeit/2026/week/1/matchups/{slug}/status",
                data={"action": "approve"})
    with Storage(db) as s:
        state = s.get_matchup_state("surfeit", "2026", 1, slug)
    assert state["status"] == "edited"  # approval refused while marker present
    client.post(f"/commissioner/surfeit/2026/week/1/matchups/{slug}/draft",
                data={"draft_text": "Clean edited preview."})
    client.post(f"/commissioner/surfeit/2026/week/1/matchups/{slug}/status",
                data={"action": "approve"})
    with Storage(db) as s:
        assert s.get_matchup_state("surfeit", "2026", 1, slug)["status"] == "approved"
    # revision request requeues for the next Claude Code pass
    client.post(f"/commissioner/surfeit/2026/week/1/matchups/{slug}/revision",
                data={"request_type": "shorter", "detail": "cut 50 words"})
    with Storage(db) as s:
        state = s.get_matchup_state("surfeit", "2026", 1, slug)
    assert state["status"] == "ready_to_draft"
    assert "shorter: cut 50 words" in state["revision_requests"]


def test_approval_logs_usage_comment_into_repetition_log(tmp_path, monkeypatch):
    import leaguepage.desk as desk_mod

    db = tmp_path / "desk.sqlite3"
    with Storage(db) as s:
        populate_league(s, get_league("surfeit"), teams=10, rounds=3, picks="complete")
        populate_matchups(s, get_league("surfeit"), week=1, teams=10)
    monkeypatch.setattr(desk_mod, "week_dir",
                        lambda league, season, week, base_dir=None:
                        tmp_path / "editorial" / season / league.slug / f"week-{week:02d}")
    client = TestClient(create_app(db))
    slug = "team-1-vs-team-2"
    draft = ("A clean preview.\n\n"
             "<!-- usage: angle=team-1-vs-team-2:competitive frame=competitive "
             "callback=none joke_family=rafale-vs-gripen -->\n")
    client.post(f"/commissioner/surfeit/2026/week/1/matchups/{slug}/draft",
                data={"draft_text": draft})
    client.post(f"/commissioner/surfeit/2026/week/1/matchups/{slug}/status",
                data={"action": "approve"})
    with Storage(db) as s:
        usage = s.recent_editorial_usage("surfeit", "2026")
    kinds = {(u["kind"], u["value"]) for u in usage}
    assert ("frame", "competitive") in kinds
    assert ("joke_family", "rafale-vs-gripen") in kinds
    assert not any(u["value"] == "none" for u in usage)
