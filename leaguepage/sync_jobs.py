"""Browser-driven Sleeper sync for the Commissioner's Desk.

Same product pattern as publish_jobs (the publish-hang lesson): the POST
returns immediately, a daemon thread does the work, the browser polls a
status endpoint, duplicate clicks join the running job. Everything runs
in-process through the existing trusted sync code — no subprocess, no
shell, no browser-supplied input anywhere near the work.

One job syncs BOTH leagues and then refreshes the private research layer
(matchup packets, prep, section authoring) for every issue workspace that
already exists for the current week, plus the draft issue's freshness-
sensitive layers are left alone. Commissioner prose, approvals, story and
award decisions, rankings, and name overrides are never touched: the
refresh path is the same one behind the Build button, which preserves
commissioner_notes.md and only creates content files when absent.

Refresh research, preserve the commissioner's words.
"""
from __future__ import annotations

import datetime as dt
import threading
import traceback
import uuid

from leaguepage.config import LEAGUES
from leaguepage.storage import Storage

_LOCK = threading.Lock()
_JOB: dict | None = None

LAST_SYNC_KEY = "last_sync_at"


def _now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def get_sync_job() -> dict | None:
    with _LOCK:
        return _JOB


def start_sync_job(db_path) -> tuple[dict, bool]:
    """(job, created). A running job is returned as-is: clicking Sync twice
    can never run two syncs at once."""
    global _JOB
    with _LOCK:
        if _JOB and _JOB["state"] == "running":
            return _JOB, False
        job = {
            "job_id": uuid.uuid4().hex[:12],
            "state": "running",
            "created_at": _now(),
            "ended_at": None,
            "stages": [
                {"key": f"sleeper:{lg.slug}", "name": f"{lg.display_name} — Sleeper data",
                 "status": "pending", "detail": ""} for lg in LEAGUES
            ] + [
                {"key": "context", "name": "Snapshots + transaction context",
                 "status": "pending", "detail": ""},
                {"key": "editorial", "name": "Refresh writing briefs and packets",
                 "status": "pending", "detail": ""},
            ],
            "summary": [],   # per-league dicts for the result panel
            "error": None,
        }
        _JOB = job
    threading.Thread(target=_run_job, args=(job, db_path), daemon=True).start()
    return job, True


def _stage(job: dict, key: str) -> dict:
    return next(st for st in job["stages"] if st["key"] == key)


def _tx_count(s: Storage, league) -> int:
    return sum(len(s.get_transactions(league.league_id, wk))
               for wk in range(0, 19))


def _run_job(job: dict, db_path) -> None:
    try:
        with Storage(db_path) as s:
            _run_stages(job, s)
        job["state"] = "succeeded" if not job["error"] else "failed"
    except Exception as exc:  # noqa: BLE001 - job boundary
        job["error"] = f"{type(exc).__name__}: {exc}"
        job["state"] = "failed"
        traceback.print_exc()
    finally:
        job["ended_at"] = _now()


def _run_stages(job: dict, s: Storage) -> None:
    from leaguepage.ingest import sync_all
    from leaguepage.team_names import sleeper_team_names

    before_names = {}
    before_tx = {}
    for lg in LEAGUES:
        try:
            before_names[lg.slug] = dict(sleeper_team_names(s, lg))
        except Exception:
            before_names[lg.slug] = {}
        before_tx[lg.slug] = _tx_count(s, lg)

    for st in job["stages"]:
        if st["key"].startswith("sleeper:"):
            st["status"] = "running"
    results = sync_all(s, weeks_back=1)
    week = s.get_meta("current_week")

    any_ok = False
    for r in results:
        st = _stage(job, f"sleeper:{r.league.slug}")
        if r.ok:
            any_ok = True
            st["status"] = "ok"
            st["detail"] = (f"{r.rosters} teams, {r.picks} picks, "
                            f"weeks {r.weeks_synced or '—'}")
        else:
            st["status"] = "failed"
            st["detail"] = r.error or "sync failed"
            job["error"] = (f"{r.league.display_name} did not sync: "
                            f"{r.error or 'unknown error'}. The other "
                            "league's data (if it synced) is kept.")

    ctx_st = _stage(job, "context")
    ed_st = _stage(job, "editorial")
    if not any_ok:
        ctx_st["status"] = ed_st["status"] = "skipped"
        return

    # snapshots + transaction context (same steps scripts/sync.py runs)
    ctx_st["status"] = "running"
    from leaguepage.matchup_analysis import weekly_scores
    from leaguepage.team_analytics import get_snapshot, record_snapshot
    from leaguepage.transaction_analysis import record_transaction_contexts

    ctx_bits = []
    for r in results:
        if not r.ok:
            continue
        data = s.get_league(r.league.league_id) or {}
        season = str(data.get("season") or "")
        if season:
            if not get_snapshot(s, r.league, season, 0):
                record_snapshot(s, r.league, season, 0)
            scores = weekly_scores(s, r.league.league_id, int(week or 1))
            played = max((len(v) for v in scores.values()), default=0)
            if played:
                record_snapshot(s, r.league, season, played)
        stored = record_transaction_contexts(s, r.league)
        if stored:
            ctx_bits.append(f"{r.league.slug}: {stored} new move context(s)")
    ctx_st["status"] = "ok"
    ctx_st["detail"] = "; ".join(ctx_bits) or "up to date"

    # refresh research for existing current-week workspaces
    ed_st["status"] = "running"
    from leaguepage.desk import refresh_issue_research
    from leaguepage.issue_builder import issue_dir

    refreshed = []
    for r in results:
        if not r.ok:
            continue
        data = s.get_league(r.league.league_id) or {}
        season = str(data.get("season") or "")
        issue_key = f"week-{int(week or 1):02d}"
        if not season:
            continue
        exists = bool(s.get_issue(r.league.slug, season, issue_key)) or \
            issue_dir(r.league, season, issue_key).exists()
        if exists:
            refresh_issue_research(s, r.league, season, issue_key)
            refreshed.append(f"{r.league.slug} {issue_key}")
    ed_st["status"] = "ok"
    ed_st["detail"] = ("refreshed " + ", ".join(refreshed)) if refreshed \
        else "no current-week workspace yet (created on first Edit)"

    # per-league result summary + renames
    for r in results:
        if not r.ok:
            job["summary"].append({"league": r.league.display_name,
                                   "ok": False, "error": r.error})
            continue
        after = {}
        try:
            from leaguepage.team_names import sleeper_team_names
            after = dict(sleeper_team_names(s, r.league))
        except Exception:
            pass
        renames = [f"{before_names[r.league.slug].get(rid) or f'Roster {rid}'}"
                   f" → {name}"
                   for rid, name in after.items()
                   if name and before_names[r.league.slug].get(rid) != name
                   and before_names[r.league.slug]]
        new_tx = _tx_count(s, r.league) - before_tx[r.league.slug]
        job["summary"].append({
            "league": r.league.display_name, "ok": True,
            "week": week, "teams": r.rosters,
            "new_transactions": max(0, new_tx),
            "renames": renames[:6],
            "warnings": r.warnings[:4],
        })

    s.set_meta(LAST_SYNC_KEY, _now())
