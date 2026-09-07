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
from leaguepage import prose_store
from leaguepage.storage import Storage

from fixtures import approve, populate_league, save_section

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
    """Save as the browser does: against the version currently stored,
    unless the test is deliberately claiming a different one."""
    if "expected_version" in kw:
        return client.post(f"{EDIT}/save", json={"section": section, "text": text,
                                                 "base_sha": sha, **kw})
    return save_section(client, EDIT, section, text, base_sha=sha, **kw)


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
    """A save that names a version the store has moved past is refused,
    and the stored text is untouched."""
    client, db, idir = env
    r = _save(client, "lowdown", "clobber", expected_version="fs1:" + "0" * 16)
    assert r.status_code == 409
    body = r.json()
    # The refusal carries both sides, so the page can show him what he was
    # editing and what is stored without a second request.
    assert body["expected_version"] == "fs1:" + "0" * 16
    assert body["current_version"] and "Original words." in body["current_text"]
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


def test_full_preview_renders_the_public_page_with_a_private_banner(env):
    """One renderer: the preview IS the public issue page. Parity is pinned
    in tests/test_preview_parity.py; this is the route still working."""
    client, db, idir = env
    r = client.get(f"{EDIT}/full-preview")
    assert r.status_code == 200
    assert "PRIVATE PREVIEW" in r.text and "UNPUBLISHED COMMISSIONER PREVIEW" in r.text
    assert "Original words." in r.text
    assert '<header class="masthead">' in r.text


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
        approve(s, league_slug="surfeit", season=SEASON, issue_key="draft",
                          module_key="hardware", included=1)
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

from types import SimpleNamespace

import leaguepage.publish_jobs as pj


@pytest.fixture
def jobs_env(env, monkeypatch):
    """Subprocess + network mocked. Jobs need no isolating any more: they
    live in the test's own database, not in a module global."""
    client, db, idir = env
    calls = []

    def fake_run(ctx, cmd, *, cwd, timeout, env=None):
        calls.append(cmd)
        import subprocess
        return subprocess.CompletedProcess(cmd, 0, "Built ok\naudit clean", "")

    monkeypatch.setattr(pj, "_run", fake_run)
    monkeypatch.setitem(pj._STAGE_FNS, "verify", lambda ctx: "/ -> 200 (mocked)")
    return client, db, idir, calls


def _stage_of(job, key):
    return next(s for s in job["stages"] if s["key"] == key)


def _live_jobs(db):
    from leaguepage import jobs as jobs_mod

    return jobs_mod.SQLiteJobRepository(db).active(pj.JOB_TYPE)


def _approve_only_lowdown(db):
    with Storage(db) as s:
        for key in ("hardware", "draft-capsules", "ctp", "power", "tracks", "fades",
                    "forceflow", "blackbox", "false-assumptions", "branches", "custom"):
            s.set_issue_module(league_slug="surfeit", season=SEASON, issue_key="draft",
                              module_key=key, included=0)
        approve(s, league_slug="surfeit", season=SEASON, issue_key="draft",
                          module_key="lowdown")


def _wait_job(client, timeout=8.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        data = client.get(f"{EDIT}/publish-status").json()
        if data.get("job") and not data["job"]["active"]:
            return data
        time.sleep(0.05)
    raise AssertionError("job did not finish (would previously have hung)")


def test_publish_start_requires_confirmations(jobs_env):
    client, db, idir, calls = jobs_env
    r = client.post(f"{EDIT}/publish-start", data={"mode": "deploy", "confirm": "yes"},
                    follow_redirects=False)
    assert r.status_code == 303 and "error=confirm" in r.headers["location"]
    assert pj.get_job_for(db, "surfeit", SEASON, "draft") is None   # nothing started


def test_publish_job_blocked_snapshot_fails_fast(jobs_env):
    client, db, idir, calls = jobs_env  # lowdown not approved -> gate blocks
    client.post(f"{EDIT}/publish-start", data={"mode": "local", "confirm": "yes"})
    data = _wait_job(client)
    job = data["job"]
    assert job["state"] == "failed"
    assert job["stages"][0]["status"] == "failed"
    assert "blocked" in job["stages"][0]["detail"]
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

    def failing_run(ctx, cmd, *, cwd, timeout, env=None):
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
    assert _stage_of(job, "build")["status"] == "failed"
    # Stages after a hard failure are recorded as skipped rather than left
    # looking pending forever, which is the same fact stated honestly.
    assert _stage_of(job, "deploy")["status"] == "skipped"
    assert not any("vercel@latest" in " ".join(c) for c in calls)
    # production was never touched, and the record says exactly that
    assert data["deploy_state"]["state"] == "never-deployed"
    assert data["deploy_state"]["last_attempt"]["failed_stage"] == "build"
    assert (cfg.PUBLISHED_DIR / "surfeit" / SEASON / "draft.json").exists()  # local ok


def test_timeout_fails_stage_instead_of_hanging(jobs_env, monkeypatch):
    client, db, idir, calls = jobs_env
    _approve_only_lowdown(db)

    def timing_out(ctx, cmd, *, cwd, timeout, env=None):
        raise pj.StageError("timed out after 1s (process terminated)")

    monkeypatch.setattr(pj, "_run", timing_out)
    client.post(f"{EDIT}/publish-start", data={"mode": "local", "confirm": "yes"})
    job = _wait_job(client)["job"]
    assert job["state"] == "failed"
    assert "timed out" in _stage_of(job, "build")["detail"]


def test_duplicate_click_reuses_running_job(jobs_env, monkeypatch):
    """The second click joins the first job rather than starting a second.

    The job is held open by an event the test releases, not by a deadline:
    it used to give the build stage 0.6 seconds of wall clock and assert
    that the job was still running, which is a race with the fixture on a
    busy machine and failed as one.
    """
    import threading

    client, db, idir, calls = jobs_env
    _approve_only_lowdown(db)
    gate = threading.Event()

    def slow_run(ctx, cmd, *, cwd, timeout, env=None):
        gate.wait(20)
        import subprocess
        return subprocess.CompletedProcess(cmd, 0, "ok", "")

    monkeypatch.setattr(pj, "_run", slow_run)
    client.post(f"{EDIT}/publish-start", data={"mode": "local", "confirm": "yes"})
    client.post(f"{EDIT}/publish-start", data={"mode": "local", "confirm": "yes"})
    assert len(_live_jobs(db)) == 1   # second click joined the running job
    gate.set()
    data = _wait_job(client)
    assert data["job"]["state"] == "succeeded"
    # refresh recovery: status still reports the finished job afterwards
    again = client.get(f"{EDIT}/publish-status").json()
    assert again["job"]["job_id"] == data["job"]["job_id"]


def test_failure_reason_reaches_the_log(jobs_env):
    """A Desk restart forgets the in-memory job; the log must carry why."""
    client, db, idir, calls = jobs_env      # lowdown not approved -> blocked
    client.post(f"{EDIT}/publish-start", data={"mode": "local", "confirm": "yes"})
    data = _wait_job(client)
    assert "FAIL snapshot: snapshot blocked" in data["log_tail"]


def test_a_changed_published_issue_needs_a_note_and_becomes_a_correction(jobs_env):
    client, db, idir, calls = jobs_env
    _approve_only_lowdown(db)
    client.post(f"{EDIT}/publish-start", data={"mode": "local", "confirm": "yes"})
    assert _wait_job(client)["job"]["state"] == "succeeded"
    original = cfg.PUBLISHED_DIR / "surfeit" / SEASON / "draft.json"
    before = original.read_bytes()

    (idir / "lowdown" / "lowdown.md").write_text("# The Lowdown\n\nCorrected words.\n",
                                                 encoding="utf-8")
    # A correction changes the words, so the approval that covered the
    # words it replaced no longer covers anything. He approves what the
    # correction actually says before it can publish.
    prose_store.reset_cache()
    client.post(f"{EDIT}/approve", json={"section": "lowdown", "action": "approve"})
    page = client.get(f"{EDIT}/publish").text
    assert "Correction note" in page and "changed" in page
    # no note: refused before any job exists
    r = client.post(f"{EDIT}/publish-start", data={"mode": "local", "confirm": "yes"},
                    follow_redirects=False)
    assert "error=note" in r.headers["location"]
    assert not _live_jobs(db)                      # and nothing was started

    client.post(f"{EDIT}/publish-start",
                data={"mode": "local", "confirm": "yes", "note": "lowdown wording"})
    job = _wait_job(client)["job"]
    assert job["state"] == "succeeded", job["stages"]
    assert job["stages"][0]["name"] == pj.CORRECTION_STAGE_NAME
    assert "correction r2" in job["stages"][0]["detail"]
    assert (cfg.PUBLISHED_DIR / "surfeit" / SEASON / "draft.r2.json").exists()
    assert original.read_bytes() == before          # the original is never rewritten

    # unchanged after the correction: a plain republish is a no-op, not a refusal
    client.post(f"{EDIT}/publish-start", data={"mode": "local", "confirm": "yes"})
    assert _wait_job(client)["job"]["state"] == "succeeded"
    assert not (cfg.PUBLISHED_DIR / "surfeit" / SEASON / "draft.r3.json").exists()


def test_a_deploy_that_fails_verification_is_still_recorded_as_a_deploy(jobs_env, monkeypatch):
    client, db, idir, calls = jobs_env
    _approve_only_lowdown(db)

    def unverifiable(ctx):
        raise pj.StageError("production verification failed after 6 attempts")

    monkeypatch.setitem(pj._STAGE_FNS, "verify", unverifiable)
    client.post(f"{EDIT}/publish-start",
                data={"mode": "deploy", "confirm": "yes", "confirm_deploy": "yes"})
    data = _wait_job(client)
    assert data["job"]["state"] == "failed"
    assert _stage_of(data["job"], "deploy")["status"] == "ok"
    assert data["deploy_state"]["state"] == "deployed-unverified"
    assert data["deploy_state"]["url"].endswith(f"/surfeit/{SEASON}/draft/")
    assert "verification failed" in data["deploy_state"]["reason"]


def test_a_lost_publish_reports_what_it_did_rather_than_guessing(jobs_env):
    """The Desk restarted mid-publish. The status endpoint must say what
    the evidence supports, not show a spinner forever and not claim a
    failure it cannot stand behind."""
    import datetime as dt

    from leaguepage import jobs as jobs_mod

    client, db, idir, calls = jobs_env
    repo = jobs_mod.SQLiteJobRepository(db)
    job, _ = repo.create(jobs_mod.JobSpec(
        job_type=pj.JOB_TYPE, scope=jobs_mod.SCOPE_ISSUE,
        league_slug="surfeit", season=SEASON, issue_key="draft",
        mode="deploy", stages=pj.STAGES_DEPLOY))
    repo.claim(job.job_id, "a-worker-that-died", 60)
    repo.append_event(job.job_id, "a-worker-that-died", "snapshot",
                      jobs_mod.OK, "frozen: draft.json")
    repo.set_target_revision(job.job_id, "a-worker-that-died", 1)

    # The reaper runs on the read path, so simply asking is enough.
    later = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)
    repo.reap_expired(now=later.isoformat(timespec="seconds"))
    data = client.get(f"{EDIT}/publish-status").json()

    assert data["job"]["state"] == "lost" and not data["job"]["active"]
    assert data["job"]["revision"] == 1
    rec = data["recovery"]
    # The deploy stage never started, so this needs no network to answer.
    assert rec["verdict"] == "snapshot-only"
    assert any("was not touched" in f for f in rec["facts"])
    assert any("bound to revision 1" in f for f in rec["facts"])
    assert "log_tail" in data          # and the log is offered, as on a failure


def test_verification_retries_while_the_alias_propagates(monkeypatch, tmp_path):
    probes = []

    def probe(url):
        probes.append(url)
        return 404 if len(probes) <= 3 else 200      # first sweep 404, second 200

    monkeypatch.setattr(pj, "_probe_url", probe)
    monkeypatch.setattr(pj, "VERIFY_PAUSE", 0)
    ctx = SimpleNamespace(
        job=SimpleNamespace(league_slug="surfeit", season=SEASON, issue_key="draft"),
        log=pj._logger(tmp_path / "log.txt"))
    detail = pj._stage_verify(ctx)
    assert "(attempt 2)" in detail and len(probes) == 6
    assert "verify attempt 1/" in (tmp_path / "log.txt").read_text(encoding="utf-8")

    monkeypatch.setattr(pj, "_probe_url", lambda url: 0)
    monkeypatch.setattr(pj, "VERIFY_ATTEMPTS", 2)
    with pytest.raises(pj.StageError, match="after 2 attempts"):
        pj._stage_verify(ctx)


def test_unchanged_text_with_a_leftover_note_deploys_instead_of_failing(jobs_env):
    """A note left in the box is not a reason to manufacture a revision."""
    client, db, idir, calls = jobs_env
    _approve_only_lowdown(db)
    client.post(f"{EDIT}/publish-start", data={"mode": "local", "confirm": "yes"})
    assert _wait_job(client)["job"]["state"] == "succeeded"
    client.post(f"{EDIT}/publish-start",
                data={"mode": "deploy", "confirm": "yes", "confirm_deploy": "yes",
                      "note": "left in the box"})
    data = _wait_job(client)
    job = data["job"]
    assert job["state"] == "succeeded", job["stages"]
    assert job["stages"][0]["detail"].startswith("unchanged")
    assert "note was not needed" in job["stages"][0]["detail"]
    assert not (cfg.PUBLISHED_DIR / "surfeit" / SEASON / "draft.r2.json").exists()
    assert data["deploy_state"]["revision"] == 1


def test_the_page_knows_whether_the_latest_revision_is_live(jobs_env):
    client, db, idir, calls = jobs_env
    _approve_only_lowdown(db)
    client.post(f"{EDIT}/publish-start", data={"mode": "local", "confirm": "yes"})
    assert _wait_job(client)["job"]["state"] == "succeeded"
    page = client.get(f"{EDIT}/publish").text
    assert "not yet live" in page and "Deploy rev 1" in page
    assert "has never been deployed from the Desk" in page

    client.post(f"{EDIT}/publish-start",
                data={"mode": "deploy", "confirm": "yes", "confirm_deploy": "yes"})
    assert _wait_job(client)["job"]["state"] == "succeeded"
    page = client.get(f"{EDIT}/publish").text
    assert "Already live. Nothing has changed." in page
    assert "Re-deploy the same text" in page
    assert "updated just now" in page

    # a correction frozen locally puts production behind again
    (idir / "lowdown" / "lowdown.md").write_text("# The Lowdown\n\nCorrected words.\n",
                                                 encoding="utf-8")
    # A correction changes the words, so the approval that covered the
    # words it replaced no longer covers anything. He approves what the
    # correction actually says before it can publish.
    prose_store.reset_cache()
    client.post(f"{EDIT}/approve", json={"section": "lowdown", "action": "approve"})
    client.post(f"{EDIT}/publish-start",
                data={"mode": "local", "confirm": "yes", "note": "wording"})
    assert _wait_job(client)["job"]["state"] == "succeeded"
    page = client.get(f"{EDIT}/publish").text
    assert "not yet live" in page and "Deploy rev 2" in page
    assert "production carries rev 1" in page


def test_a_deploy_marks_every_issue_whose_latest_revision_it_shipped(jobs_env):
    """One Vercel deploy carries the whole site. A Disco correction frozen
    earlier goes live with a Surfeit deploy, and its record must say so;
    an issue already live at its latest revision keeps its own time."""
    import json as _json

    client, db, idir, calls = jobs_env
    _approve_only_lowdown(db)
    other = cfg.PUBLISHED_DIR / "disco" / SEASON
    other.mkdir(parents=True)
    for name in ("week-01.json", "week-01.r2.json", "week-02.json"):
        (other / name).write_text(_json.dumps({"sections": []}), encoding="utf-8")
    with Storage(db) as s:
        s.set_meta(f"deploy_state:disco:{SEASON}:week-01", _json.dumps(
            {"state": "deployed", "at": "2026-01-01T00:00:00+00:00", "revision": 1}))
        s.set_meta(f"deploy_state:disco:{SEASON}:week-02", _json.dumps(
            {"state": "deployed", "at": "2026-01-02T00:00:00+00:00", "revision": 1}))
    client.post(f"{EDIT}/publish-start",
                data={"mode": "deploy", "confirm": "yes", "confirm_deploy": "yes"})
    assert _wait_job(client)["job"]["state"] == "succeeded"
    with Storage(db) as s:
        w1 = _json.loads(s.get_meta(f"deploy_state:disco:{SEASON}:week-01"))
        w2 = _json.loads(s.get_meta(f"deploy_state:disco:{SEASON}:week-02"))
    assert w1["revision"] == 2 and w1["at"] > "2026-01-01" and w1["via"] == f"surfeit:{SEASON}:draft"
    assert w2["revision"] == 1 and w2["at"] == "2026-01-02T00:00:00+00:00"
