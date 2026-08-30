"""Browser sync job + human-facing name resolution."""
from __future__ import annotations

import threading
import time

import pytest
from fastapi.testclient import TestClient

from leaguepage import sync_jobs
from leaguepage.config import get_league
from leaguepage.desk import create_app
from leaguepage.ingest import SyncResult
from leaguepage.storage import Storage

from fixtures import populate_league


@pytest.fixture(autouse=True)
def _reset_job(tmp_path, monkeypatch):
    """Fresh job state, and editorial paths isolated so a job's refresh
    stage can never touch the real workspace from a synthetic test DB."""
    import leaguepage.issue_builder as ib
    import leaguepage.matchup_packet as mp

    monkeypatch.setattr(ib, "EDITORIAL_DIR", tmp_path / "editorial")
    monkeypatch.setattr(mp, "EDITORIAL_DIR", tmp_path / "editorial")
    monkeypatch.setattr(mp, "load_managers", lambda: {})
    sync_jobs._JOB = None
    yield
    sync_jobs._JOB = None


@pytest.fixture
def world(tmp_path):
    db = tmp_path / "t.sqlite3"
    with Storage(db) as s:
        populate_league(s, get_league("surfeit"), teams=10, rounds=3)
        populate_league(s, get_league("disco"), teams=12, rounds=3)
        s.set_meta("current_week", "1")
    return db


def _fake_sync_all(ok=(True, True)):
    from leaguepage.config import LEAGUES

    def fake(storage, *, weeks_back=1, refresh_players=True):
        storage.set_meta("current_week", "1")
        out = []
        for lg, good in zip(LEAGUES, ok):
            out.append(SyncResult(league=lg, ok=good, rosters=10, picks=150,
                                  weeks_synced=[1],
                                  error=None if good else "Sleeper API 503"))
        return out
    return fake


def _wait(job, timeout=10.0):
    start = time.time()
    while job["state"] == "running" and time.time() - start < timeout:
        time.sleep(0.05)
    return job


def test_sync_job_succeeds_and_stamps_timestamp(world, monkeypatch):
    import leaguepage.ingest as ingest

    monkeypatch.setattr(ingest, "sync_all", _fake_sync_all())
    job, created = sync_jobs.start_sync_job(world)
    assert created
    _wait(job)
    assert job["state"] == "succeeded"
    assert all(st["status"] in ("ok", "skipped") for st in job["stages"])
    with Storage(world) as s:
        assert s.get_meta(sync_jobs.LAST_SYNC_KEY)
    assert len(job["summary"]) == 2 and all(x["ok"] for x in job["summary"])


def test_duplicate_click_joins_running_job(world, monkeypatch):
    import leaguepage.ingest as ingest

    gate = threading.Event()

    def slow(storage, **kw):
        gate.wait(5)
        return _fake_sync_all()(storage, **kw)
    monkeypatch.setattr(ingest, "sync_all", slow)
    job1, created1 = sync_jobs.start_sync_job(world)
    job2, created2 = sync_jobs.start_sync_job(world)
    assert created1 and not created2
    assert job1["job_id"] == job2["job_id"]
    gate.set()
    assert _wait(job1)["state"] == "succeeded"


def test_one_league_failure_is_not_reported_as_success(world, monkeypatch):
    import leaguepage.ingest as ingest

    monkeypatch.setattr(ingest, "sync_all", _fake_sync_all(ok=(True, False)))
    job, _ = sync_jobs.start_sync_job(world)
    _wait(job)
    assert job["state"] == "failed"
    assert "did not sync" in job["error"]
    ok_leagues = [x for x in job["summary"] if x["ok"]]
    bad = [x for x in job["summary"] if not x["ok"]]
    assert len(ok_leagues) == 1 and len(bad) == 1
    assert "503" in bad[0]["error"]


def test_desk_exposes_sync_control_and_status(world, monkeypatch):
    import leaguepage.ingest as ingest

    monkeypatch.setattr(ingest, "sync_all", _fake_sync_all())
    c = TestClient(create_app(world))
    home = c.get("/commissioner")
    assert "SYNC SLEEPER" in home.text
    r = c.post("/commissioner/sync-start", follow_redirects=False)
    assert r.status_code == 303          # control returns immediately
    job = sync_jobs.get_sync_job()
    _wait(job)
    status = c.get("/commissioner/sync-status").json()
    assert status["job"]["state"] == "succeeded"
    home = c.get("/commissioner")
    assert "Synced" in home.text


def test_refresh_preserves_commissioner_prose(world, tmp_path, monkeypatch):
    import leaguepage.issue_builder as ib
    import leaguepage.matchup_packet as mp

    monkeypatch.setattr(ib, "EDITORIAL_DIR", tmp_path / "editorial")
    monkeypatch.setattr(mp, "EDITORIAL_DIR", tmp_path / "editorial")
    from leaguepage.desk import refresh_issue_research
    from leaguepage.issue_builder import issue_dir

    lg = get_league("surfeit")
    ldir = issue_dir(lg, "2026", "week-01") / "lowdown"
    ldir.mkdir(parents=True)
    my_words = "# The Lowdown\n\nMy own opening sentence stays mine.\n"
    (ldir / "lowdown.md").write_text(my_words, encoding="utf-8")
    with Storage(world) as s:
        refresh_issue_research(s, lg, "2026", "week-01")
    assert (ldir / "lowdown.md").read_text(encoding="utf-8") == my_words


def test_sync_endpoint_never_in_public_build(tmp_path, monkeypatch):
    import leaguepage.issue_builder as ib
    import leaguepage.matchup_packet as mp
    from leaguepage.site_build import build_site

    monkeypatch.setattr(ib, "EDITORIAL_DIR", tmp_path / "editorial")
    monkeypatch.setattr(mp, "EDITORIAL_DIR", tmp_path / "editorial")
    monkeypatch.setattr(mp, "load_managers", lambda: {})
    db = tmp_path / "t.sqlite3"
    with Storage(db) as s:
        populate_league(s, get_league("surfeit"), teams=10, rounds=3)
        populate_league(s, get_league("disco"), teams=12, rounds=3)
        s.set_meta("current_week", "1")
        build_site(s, out_dir=tmp_path / "dist",
                   published_dir=tmp_path / "published",
                   editorial_dir=tmp_path / "editorial")
    for f in (tmp_path / "dist").rglob("*"):
        if f.is_file() and f.suffix in (".html", ".js", ".json"):
            assert "sync-start" not in f.read_text(encoding="utf-8", errors="ignore")


# ------------------------------------------------- name resolution rules

def test_display_name_precedence(world):
    from leaguepage.matchup_analysis import analyze_week
    from fixtures import populate_matchups

    lg = get_league("surfeit")
    with Storage(world) as s:
        users = s.get_league_users(lg.league_id)
        for u in users:
            if u["user_id"] == "u2":
                u["metadata"] = {}           # roster 2: no Sleeper name
            if u["user_id"] == "u3":
                u["metadata"] = {}           # roster 3: no name, no override
        s.save_league_users(lg.league_id, users)
        s.set_public_team_name(lg.slug, 2, "Commissioner Choice")
        populate_matchups(s, lg, week=1, teams=10)
        analysis = analyze_week(s, lg, 1)
    teams = {t["roster_id"]: t for t in analysis["teams"].values()}
    assert teams[2]["display_name"] == "Commissioner Choice"   # override wins
    assert teams[2]["team_slug"] == "roster-2"                 # slug stays stable
    assert teams[1]["display_name"] == "Team 1"                # Sleeper name
    assert teams[3]["display_name"] == "Roster 3"              # neutral fallback


def test_story_headlines_use_display_names(world):
    from leaguepage.matchup_analysis import analyze_week
    from leaguepage.weekly_signals import weekly_story_candidates
    from leaguepage.matchup_packet import compute_week
    from fixtures import populate_matchups

    lg = get_league("surfeit")
    with Storage(world) as s:
        users = s.get_league_users(lg.league_id)
        for u in users:
            if u["user_id"] in ("u1", "u2"):
                u["metadata"] = {}
        s.save_league_users(lg.league_id, users)
        s.set_public_team_name(lg.slug, 1, "Named By Override")
        populate_matchups(s, lg, week=1, teams=10,
                          scores={rid: 80.0 + rid for rid in range(1, 11)})
        computed = compute_week(s, lg, 1)
        cands = weekly_story_candidates(s, lg, 1, computed)
    heads = " | ".join(c["headline"] for c in cands)
    assert "Named By Override" in heads
    assert "roster-1 " not in heads and ": roster-1" not in heads