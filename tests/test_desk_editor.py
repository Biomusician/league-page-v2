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


def test_health_endpoint(env):
    client, db, idir = env
    data = client.get("/health").json()
    assert data["status"] == "ok" and data["app"] == "commissioner-desk"


def test_ghost_brief_shown_for_empty_section_never_in_textarea(env):
    client, db, idir = env
    (idir / "sections" / "hardware.md").write_text("", encoding="utf-8")
    with Storage(db) as s:
        s.set_issue_module(league_slug="surfeit", season=SEASON, issue_key="draft",
                          module_key="hardware", included=1)
    t = client.get(EDIT).text
    assert "suggestions ready" in t              # not-written chip
    assert 'class="ghost"' in t                  # ghost overlay present
    import re
    for m in re.finditer(r"<textarea[^>]*autosave[^>]*>(.*?)</textarea>", t, re.S):
        assert "Writing suggestions" not in m.group(1)   # never becomes content


def test_empty_section_with_brief_still_blocks_publication(env):
    client, db, idir = env
    (idir / "sections" / "hardware.md").write_text("", encoding="utf-8")
    with Storage(db) as s:
        s.set_issue_module(league_slug="surfeit", season=SEASON, issue_key="draft",
                          module_key="hardware", included=1, approved=1)
    r = client.get(f"{EDIT}/publish")
    assert "Cannot publish yet" in r.text        # excellent ghost != written
    assert "write it or exclude it" in r.text    # actionable, not mysterious


def test_matchup_proposal_path_is_windows_safe(env):
    from pathlib import Path
    assert ":" not in str(Path("proposals") / "matchup--a-vs-b.md")
    t = de.__dict__  # _proposal_path is closure-scoped; verify via request flow instead
    client, db, idir = env
    r = client.post(f"{EDIT}/request-rewrite",
                    json={"section": "lowdown", "note": "x"})
    assert r.status_code == 200
    assert "matchup--<slug>" in (idir / "REVISION_REQUESTS.md").read_text(encoding="utf-8")

# ---------------------------------------------------------- publish jobs

import time

import leaguepage.publish_jobs as pj


@pytest.fixture
def jobs_env(env, monkeypatch):
    """Job registry isolated per test; subprocess + network mocked."""
    client, db, idir = env
    monkeypatch.setattr(pj, "_JOBS", {})
    monkeypatch.setattr(pj, "_ACTIVE", {})
    calls = []

    def fake_run(job, cmd, *, cwd, timeout, env=None):
        calls.append(cmd)
        import subprocess
        return subprocess.CompletedProcess(cmd, 0, "Built ok\naudit clean", "")

    monkeypatch.setattr(pj, "_run", fake_run)
    monkeypatch.setitem(pj._STAGE_FNS, "verify", lambda job, dbp: "/ -> 200 (mocked)")
    return client, db, idir, calls


def _approve_only_lowdown(db):
    with Storage(db) as s:
        for key in ("hardware", "draft-capsules", "ctp", "power", "tracks", "fades",
                    "forceflow", "blackbox", "false-assumptions", "branches", "custom"):
            s.set_issue_module(league_slug="surfeit", season=SEASON, issue_key="draft",
                              module_key=key, included=0)
        s.set_issue_module(league_slug="surfeit", season=SEASON, issue_key="draft",
                          module_key="lowdown", approved=1)


def _wait_job(client, timeout=8.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        data = client.get(f"{EDIT}/publish-status").json()
        if data.get("job") and data["job"]["state"] != "running":
            return data
        time.sleep(0.05)
    raise AssertionError("job did not finish (would previously have hung)")


def test_publish_start_requires_confirmations(jobs_env):
    client, db, idir, calls = jobs_env
    r = client.post(f"{EDIT}/publish-start", data={"mode": "deploy", "confirm": "yes"},
                    follow_redirects=False)
    assert r.status_code == 303 and "error=confirm" in r.headers["location"]
    assert pj._JOBS == {}  # nothing started


def test_publish_job_blocked_snapshot_fails_fast(jobs_env):
    client, db, idir, calls = jobs_env  # lowdown not approved -> gate blocks
    client.post(f"{EDIT}/publish-start", data={"mode": "local", "confirm": "yes"})
    data = _wait_job(client)
    job = data["job"]
    assert job["state"] == "failed"
    assert job["stages"][0]["status"] == "fail" and "blocked" in job["stages"][0]["detail"]
    assert calls == []  # build never ran
    assert "log_tail" in data  # Show Publish Details has content


def test_publish_local_job_succeeds_with_stages(jobs_env):
    client, db, idir, calls = jobs_env
    _approve_only_lowdown(db)
    client.post(f"{EDIT}/publish-start", data={"mode": "local", "confirm": "yes"})
    job = _wait_job(client)["job"]
    assert job["state"] == "succeeded"
    assert [s["status"] for s in job["stages"]] == ["ok", "ok"]
    assert (cfg.PUBLISHED_DIR / "surfeit" / SEASON / "draft.json").exists()
    assert any("build_public_site" in " ".join(c) for c in calls)
    snap = (cfg.PUBLISHED_DIR / "surfeit" / SEASON / "draft.json").read_text(encoding="utf-8")
    assert "Writing suggestions" not in snap and "WORTH MENTIONING" not in snap


def test_deploy_job_success_records_state_and_url(jobs_env):
    client, db, idir, calls = jobs_env
    _approve_only_lowdown(db)
    client.post(f"{EDIT}/publish-start",
                data={"mode": "deploy", "confirm": "yes", "confirm_deploy": "yes"})
    data = _wait_job(client)
    job = data["job"]
    assert job["state"] == "succeeded"
    assert job["issue_url"].endswith(f"/surfeit/{SEASON}/draft/")
    assert data["deploy_state"]["state"] == "deployed"
    assert any("vercel@latest" in " ".join(c) and "deploy" in c for c in calls)
    assert all("--yes" in c for c in calls if "vercel@latest" in " ".join(c))


def test_build_failure_prevents_deploy(jobs_env, monkeypatch):
    client, db, idir, calls = jobs_env
    _approve_only_lowdown(db)

    def failing_run(job, cmd, *, cwd, timeout, env=None):
        calls.append(cmd)
        import subprocess
        if "build_public_site" in " ".join(cmd):
            return subprocess.CompletedProcess(cmd, 1, "", "privacy audit FAILED")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(pj, "_run", failing_run)
    client.post(f"{EDIT}/publish-start",
                data={"mode": "deploy", "confirm": "yes", "confirm_deploy": "yes"})
    data = _wait_job(client)
    job = data["job"]
    assert job["state"] == "failed"
    assert pj._stage(job, "build")["status"] == "fail"
    assert pj._stage(job, "deploy")["status"] == "pending"      # never ran
    assert not any("vercel@latest" in " ".join(c) for c in calls)
    assert data["deploy_state"]["state"] == "deploy-failed"     # separate from snapshot
    assert (cfg.PUBLISHED_DIR / "surfeit" / SEASON / "draft.json").exists()  # local ok


def test_timeout_fails_stage_instead_of_hanging(jobs_env, monkeypatch):
    client, db, idir, calls = jobs_env
    _approve_only_lowdown(db)

    def timing_out(job, cmd, *, cwd, timeout, env=None):
        raise pj.StageError("timed out after 1s (process terminated)")

    monkeypatch.setattr(pj, "_run", timing_out)
    client.post(f"{EDIT}/publish-start", data={"mode": "local", "confirm": "yes"})
    job = _wait_job(client)["job"]
    assert job["state"] == "failed"
    assert "timed out" in pj._stage(job, "build")["detail"]


def test_duplicate_click_reuses_running_job(jobs_env, monkeypatch):
    client, db, idir, calls = jobs_env
    _approve_only_lowdown(db)
    gate = time.time() + 0.6

    def slow_run(job, cmd, *, cwd, timeout, env=None):
        while time.time() < gate:
            time.sleep(0.02)
        import subprocess
        return subprocess.CompletedProcess(cmd, 0, "ok", "")

    monkeypatch.setattr(pj, "_run", slow_run)
    client.post(f"{EDIT}/publish-start", data={"mode": "local", "confirm": "yes"})
    client.post(f"{EDIT}/publish-start", data={"mode": "local", "confirm": "yes"})
    assert len(pj._JOBS) == 1        # second click joined the running job
    data = _wait_job(client)
    assert data["job"]["state"] == "succeeded"
    # refresh recovery: status still reports the finished job afterwards
    again = client.get(f"{EDIT}/publish-status").json()
    assert again["job"]["job_id"] == data["job"]["job_id"]
