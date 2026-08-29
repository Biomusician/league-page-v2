from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import leaguepage.config as cfg
import leaguepage.desk_editor as de
import leaguepage.issue_builder as ib
import leaguepage.matchup_packet as mp
import leaguepage.publish as pub
from leaguepage.config import get_league
from leaguepage.desk import create_app
from leaguepage.matchup_packet import ROUGH_DRAFT_MARKER
from leaguepage.storage import Storage

from fixtures import populate_league

SEASON = "2027"
LG = get_league("surfeit")


@pytest.fixture
def env(tmp_path, monkeypatch):
    ed = tmp_path / "editorial"
    monkeypatch.setattr(ib, "EDITORIAL_DIR", ed)
    monkeypatch.setattr(mp, "EDITORIAL_DIR", ed)
    monkeypatch.setattr(cfg, "PUBLISHED_DIR", tmp_path / "published")
    db = tmp_path / "t.sqlite3"
    with Storage(db) as s:
        populate_league(s, LG, teams=10, rounds=3, picks="complete", season=SEASON)
        s.set_meta("current_week", "1")
    idir = ed / SEASON / LG.slug / "draft"
    (idir / "lowdown").mkdir(parents=True)
    (idir / "lowdown" / "lowdown.md").write_text("# The Lowdown\n\nOriginal words.\n",
                                                 encoding="utf-8")
    (idir / "lowdown" / "rough-lowdown.md").write_text(
        f"<!-- {ROUGH_DRAFT_MARKER} -->\n# The Lowdown\n\nGenerated rough.\n",
        encoding="utf-8")
    (idir / "sections").mkdir()
    (idir / "sections" / "draft-capsules.md").write_text(
        "## Capsules\n\nintro\n\n### Team One\n\nalpha text\n\n### Team Two\n\nbeta text\n",
        encoding="utf-8")
    client = TestClient(create_app(db_path=db))
    return client, db, idir


EDIT = f"/commissioner/surfeit/{SEASON}/issue/draft/edit"


def _save(client, section, text, sha="", **kw):
    return client.post(f"{EDIT}/save", json={"section": section, "text": text,
                                             "base_sha": sha, **kw})


def test_editor_page_loads_with_cards(env):
    client, db, idir = env
    r = client.get(EDIT)
    assert r.status_code == 200
    assert 'id="sec-lowdown"' in r.text and 'id="sec-draft-capsules"' in r.text
    assert "Original words." in r.text


def test_save_reload_revision_and_state(env):
    client, db, idir = env
    r = _save(client, "lowdown", "# The Lowdown\n\nEdited words.\n",
              de._sha("# The Lowdown\n\nOriginal words.\n"))
    assert r.status_code == 200 and r.json()["state"] == "commissioner-edited"
    assert "Edited words." in (idir / "lowdown" / "lowdown.md").read_text(encoding="utf-8")
    assert "Edited words." in client.get(EDIT).text  # persists across reload
    with Storage(db) as s:
        revs = s.get_prose_revisions("surfeit", SEASON, "draft", "lowdown")
        assert revs and "Original words." in revs[0]["prior_text"]
        assert s.get_prose_states("surfeit", SEASON, "draft")["lowdown"] == "commissioner-edited"


def test_stale_sha_conflicts_and_preserves_file(env):
    client, db, idir = env
    r = _save(client, "lowdown", "clobber", "0" * 16)
    assert r.status_code == 409
    assert "Original words." in (idir / "lowdown" / "lowdown.md").read_text(encoding="utf-8")


def test_chunk_save_touches_only_that_chunk(env):
    client, db, idir = env
    text = (idir / "sections" / "draft-capsules.md").read_text(encoding="utf-8")
    chunks = de._split_chunks(text)
    assert len(chunks) == 3 and "".join(chunks) == text
    r = _save(client, "draft-capsules", "### Team One\n\nrewritten alpha\n\n",
              de._sha(chunks[1]), chunk_index=1, chunk_count=3)
    assert r.status_code == 200
    new = (idir / "sections" / "draft-capsules.md").read_text(encoding="utf-8")
    assert "rewritten alpha" in new and "beta text" in new and "intro" in new


def test_bad_section_name_rejected(env):
    client, db, idir = env
    assert _save(client, "../../../evil", "x").status_code == 400


def test_approve_blocked_by_marker_then_allowed(env):
    client, db, idir = env
    p = idir / "sections" / "draft-capsules.md"
    p.write_text(f"<!-- {ROUGH_DRAFT_MARKER} -->\ntext", encoding="utf-8")
    r = client.post(f"{EDIT}/approve", json={"section": "draft-capsules", "action": "approve"})
    assert r.status_code == 400 and "marker" in r.json()["error"]
    p.write_text("clean text", encoding="utf-8")
    assert client.post(f"{EDIT}/approve",
                       json={"section": "draft-capsules", "action": "approve"}).status_code == 200
    with Storage(db) as s:
        mods = s.get_issue_modules("surfeit", SEASON, "draft")
        assert mods["draft-capsules"]["approved"] == 1
        assert mods.get("lowdown", {}).get("approved", 0) == 0  # only that section


def test_restore_previous_version(env):
    client, db, idir = env
    orig_sha = de._sha("# The Lowdown\n\nOriginal words.\n")
    _save(client, "lowdown", "v2 text", orig_sha)
    with Storage(db) as s:
        rev_id = s.get_prose_revisions("surfeit", SEASON, "draft", "lowdown")[0]["id"]
    r = client.post(f"{EDIT}/restore", json={"section": "lowdown", "revision_id": rev_id})
    assert r.status_code == 200
    assert "Original words." in (idir / "lowdown" / "lowdown.md").read_text(encoding="utf-8")


def test_rewrite_request_and_proposal_accept(env):
    client, db, idir = env
    r = client.post(f"{EDIT}/request-rewrite",
                    json={"section": "lowdown", "note": "shorten by 25%"})
    assert r.status_code == 200
    reqfile = (idir / "REVISION_REQUESTS.md").read_text(encoding="utf-8")
    assert "shorten by 25%" in reqfile and "proposals/" in reqfile
    # Claude writes a proposal; commissioner text must remain untouched
    (idir / "proposals").mkdir()
    (idir / "proposals" / "lowdown.md").write_text("# The Lowdown\n\nProposed rewrite.\n",
                                                   encoding="utf-8")
    assert "Original words." in (idir / "lowdown" / "lowdown.md").read_text(encoding="utf-8")
    page = client.get(EDIT).text
    assert "Proposed rewrite." in page and "Original words." in page  # side by side
    r = client.post(f"{EDIT}/proposal", json={"section": "lowdown", "action": "accept"})
    assert r.status_code == 200
    assert "Proposed rewrite." in (idir / "lowdown" / "lowdown.md").read_text(encoding="utf-8")
    assert not (idir / "proposals" / "lowdown.md").exists()
    with Storage(db) as s:
        assert s.list_rewrite_requests("surfeit", SEASON, "draft") == []  # closed
        revs = s.get_prose_revisions("surfeit", SEASON, "draft", "lowdown")
        assert any("Original words." in r_["prior_text"] for r_ in revs)  # recoverable


def test_proposal_discard_keeps_current(env):
    client, db, idir = env
    (idir / "proposals").mkdir()
    (idir / "proposals" / "lowdown.md").write_text("proposal", encoding="utf-8")
    r = client.post(f"{EDIT}/proposal", json={"section": "lowdown", "action": "discard"})
    assert r.status_code == 200
    assert "Original words." in (idir / "lowdown" / "lowdown.md").read_text(encoding="utf-8")
    assert not (idir / "proposals" / "lowdown.md").exists()


def test_reset_to_generated_requires_confirm_and_clears_approval(env):
    client, db, idir = env
    assert client.post(f"{EDIT}/reset-generated",
                       json={"section": "lowdown"}).status_code == 400
    r = client.post(f"{EDIT}/reset-generated",
                    json={"section": "lowdown", "confirm": "yes"})
    assert r.status_code == 200
    text = (idir / "lowdown" / "lowdown.md").read_text(encoding="utf-8")
    assert "Generated rough." in text


def test_full_preview_shows_unapproved_and_banner(env):
    client, db, idir = env
    r = client.get(f"{EDIT}/full-preview")
    assert r.status_code == 200
    assert "PRIVATE COMMISSIONER PREVIEW" in r.text
    assert "Original words." in r.text


def test_publish_local_blocked_then_succeeds(env, monkeypatch):
    client, db, idir = env
    calls = []
    monkeypatch.setattr(de.subprocess, "run",
                        lambda *a, **k: calls.append(a) or type(
                            "P", (), {"returncode": 0, "stdout": "ok\nclean", "stderr": ""})())
    # lowdown not approved yet -> publish must fail, no snapshot
    r = client.post(f"{EDIT}/publish-local", data={"confirm": "yes"})
    assert "❌" in r.text and not (cfg.PUBLISHED_DIR / "surfeit").exists()
    # approve lowdown, exclude everything else
    with Storage(db) as s:
        for key in ("hardware", "draft-capsules", "ctp", "power", "tracks", "fades",
                    "forceflow", "blackbox", "false-assumptions", "branches"):
            s.set_issue_module(league_slug="surfeit", season=SEASON, issue_key="draft",
                              module_key=key, included=0)
        s.set_issue_module(league_slug="surfeit", season=SEASON, issue_key="draft",
                          module_key="lowdown", approved=1)
    r = client.post(f"{EDIT}/publish-local", data={"confirm": "yes"})
    assert "❌" not in r.text
    assert (cfg.PUBLISHED_DIR / "surfeit" / SEASON / "draft.json").exists()
    assert calls  # public build was invoked


def test_publish_deploy_stops_on_failed_build(env, monkeypatch):
    client, db, idir = env
    with Storage(db) as s:
        for key in ("hardware", "draft-capsules", "ctp", "power", "tracks", "fades",
                    "forceflow", "blackbox", "false-assumptions", "branches"):
            s.set_issue_module(league_slug="surfeit", season=SEASON, issue_key="draft",
                              module_key=key, included=0)
        s.set_issue_module(league_slug="surfeit", season=SEASON, issue_key="draft",
                          module_key="lowdown", approved=1)
    deploys = []
    def fake_run(cmd, **kw):
        if "build_public_site.py" in str(cmd):
            return type("P", (), {"returncode": 1, "stdout": "", "stderr": "audit FAILED"})()
        deploys.append(cmd)
        return type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})()
    monkeypatch.setattr(de.subprocess, "run", fake_run)
    r = client.post(f"{EDIT}/publish-deploy",
                    data={"confirm": "yes", "confirm_deploy": "yes"})
    assert "❌" in r.text and deploys == []  # deploy never ran


def test_publish_deploy_requires_both_confirmations(env):
    client, db, idir = env
    r = client.post(f"{EDIT}/publish-deploy", data={"confirm": "yes"})
    assert "Confirmation" in r.text and "❌" in r.text


def test_health_endpoint(env):
    client, db, idir = env
    data = client.get("/health").json()
    assert data["status"] == "ok" and data["app"] == "commissioner-desk"
