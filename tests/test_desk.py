from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from leaguepage.config import get_league
from leaguepage.desk import create_app
from leaguepage.storage import Storage

from fixtures import populate_league


@pytest.fixture
def client(tmp_path):
    """Desk over a temp DB with the real 'surfeit' league populated synthetically."""
    db = tmp_path / "desk.sqlite3"
    with Storage(db) as s:
        populate_league(s, get_league("surfeit"), teams=10, rounds=3, picks="complete")
        populate_league(s, get_league("disco"), teams=12, rounds=4, picks="none")
    return TestClient(create_app(db)), db


def test_home_lists_both_leagues(client):
    c, _ = client
    r = c.get("/commissioner")
    assert r.status_code == 200
    assert "DISCO CHAT" in r.text and "THE SURFEIT" in r.text


def test_review_renders_and_pre_draft_is_graceful(client):
    c, _ = client
    r = c.get("/commissioner/surfeit/2026/draft-review")
    assert r.status_code == 200 and "Story Board" in r.text
    r = c.get("/commissioner/disco/2026/draft-review")
    assert r.status_code == 200
    assert "0 picks" in r.text  # empty draft renders with warning, not a crash


def test_story_decision_persists(client):
    c, db = client
    r = c.post("/commissioner/surfeit/2026/draft-review/story",
               data={"candidate_id": "stack:team-1:somebody", "decision": "include",
                     "note": "front page"},
               follow_redirects=False)
    assert r.status_code == 303
    with Storage(db) as s:
        d = s.get_story_decisions("surfeit", "2026", "draft")
    assert d["stack:team-1:somebody"]["decision"] == "include"
    assert d["stack:team-1:somebody"]["note"] == "front page"


def test_award_decision_persists_and_updates(client):
    c, db = client
    c.post("/commissioner/surfeit/2026/draft-review/award",
           data={"award_key": "best-value", "decision": "awarded", "winner": "team-4", "note": ""})
    c.post("/commissioner/surfeit/2026/draft-review/award",
           data={"award_key": "best-value", "decision": "rejected", "winner": "", "note": "nobody deserves it"})
    with Storage(db) as s:
        d = s.get_award_decisions("surfeit", "2026", "draft")["best-value"]
    assert d["decision"] == "rejected" and d["note"] == "nobody deserves it"


def test_power_rankings_roundtrip(client):
    c, db = client
    form = {}
    for rid in range(1, 11):
        form[f"rank_{rid}"] = str(rid)
        form[f"tier_{rid}"] = "1" if rid <= 3 else "3"
        form[f"note_{rid}"] = f"note {rid}" if rid == 1 else ""
    c.post("/commissioner/surfeit/2026/draft-review/power", data=form)
    with Storage(db) as s:
        rows = s.get_power_rankings("surfeit", "2026", "preseason")
    assert len(rows) == 10
    assert rows[0]["rank"] == 1 and rows[0]["tier"] == 1 and rows[0]["note"] == "note 1"
    # re-render shows the saved values
    r = c.get("/commissioner/surfeit/2026/draft-review")
    assert 'value="note 1"' in r.text


def test_take_add_and_resolve_via_desk(client):
    c, db = client
    c.post("/commissioner/surfeit/2026/draft-review/take",
           data={"subject": "team-2", "quote": "Deepest WR room in the league.",
                 "topic": "wr-depth", "players": "Player Number4, Player Number10",
                 "confidence": "high"})
    with Storage(db) as s:
        take = s.open_takes("surfeit")[0]
    assert take["quote"] == "Deepest WR room in the league."
    c.post(f"/commissioner/surfeit/2026/draft-review/take/{take['take_id']}/resolve",
           data={"status": "too_early", "resolution": ""})
    with Storage(db) as s:
        assert s.open_takes("surfeit")[0]["status"] == "too_early"


def test_command_brief_page_renders_inside_the_temp_tree(client, tmp_path, monkeypatch):
    """The brief route rebuilds the page on every open; the file it writes
    must land in the issue directory, never anywhere else."""
    import leaguepage.issue_builder as ib
    import leaguepage.matchup_packet as mp

    monkeypatch.setattr(ib, "EDITORIAL_DIR", tmp_path / "editorial")
    monkeypatch.setattr(mp, "EDITORIAL_DIR", tmp_path / "editorial")
    c, _ = client
    r = c.get("/commissioner/surfeit/2026/issue/week-01/brief")
    assert r.status_code == 200
    assert "TOP STORIES" in r.text and "Editorial Command Brief" in r.text
    assert (tmp_path / "editorial" / "2026" / "surfeit" / "week-01" / "COMMAND_BRIEF.md").exists()
