"""Browser-driven Sleeper sync for the Commissioner's Desk.

One job syncs BOTH leagues and then refreshes the private research layer
(matchup packets, prep, section authoring) for every issue workspace that
already exists for the current week. Commissioner prose, approvals, story
and award decisions, rankings, and name overrides are never touched: the
refresh path is the same one behind the Build button, which preserves
commissioner_notes.md and only creates content files when absent.

Refresh research, preserve the commissioner's words.

The job itself is a row now, not a module global and a thread (see
`jobs`). What that buys here is small but real: pressing Sync, closing the
laptop, and coming back to a Desk that has restarted no longer shows "not
synced yet this session" while a sync it has forgotten is still running.
The button joins the live job because the idempotency key says one is
live, and the key is released when the job ends, so tomorrow's sync is
never blocked by today's.

A sync touches nothing irreversible. Every stage can be run again against
the same data, which is why this is the job type that moved first.
"""
from __future__ import annotations

import datetime as dt
import time

from leaguepage import jobs as jobs_mod
from leaguepage.config import LEAGUES
from leaguepage.job_runner import JobContext, SkipStage, SoftStageError
from leaguepage.storage import Storage

JOB_TYPE = "sync"
# Global rather than per-league: one sync covers both, so two at once would
# be two clients hitting Sleeper for the same payloads.
IDEMPOTENCY_KEY = "sync:all"

LAST_SYNC_KEY = "last_sync_at"


def _now() -> str:
    """Local time, deliberately. This one is shown to a person on the Desk
    ("Synced 2026-09-05 10:35"), not compared across machines."""
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _repo(db_path) -> jobs_mod.SQLiteJobRepository:
    return jobs_mod.SQLiteJobRepository(db_path)


def _stages() -> list[tuple[str, str]]:
    return ([(f"sleeper:{lg.slug}", f"{lg.display_name} — Sleeper data")
             for lg in LEAGUES]
            + [("context", "Snapshots + transaction context"),
               ("editorial", "Refresh writing briefs and packets")])


def as_dict(repo, job: jobs_mod.Job) -> dict:
    """The shape the Desk renders.

    Deliberately the same keys the in-memory job used to expose, so the
    home page and its polling script did not need rewriting to gain
    durability. `active` is the new one: a job can now exist before a
    worker has claimed it, and "should the button stay disabled" is that
    question rather than `state == "running"`.
    """
    result = job.result or {}
    return {"job_id": job.job_id, "state": job.state, "active": job.active,
            "created_at": job.created_at, "ended_at": job.ended_at,
            "error": job.error, "error_code": job.error_code,
            "stages": jobs_mod.fold_stages(repo.events(job.job_id)),
            "summary": result.get("summary", []),
            "timings": result.get("timings", {})}


def get_sync_job(db_path) -> dict | None:
    repo = _repo(db_path)
    # Reading job state is also when dead leases are expired. One indexed
    # UPDATE that almost always matches nothing, in exchange for the Desk
    # never showing a sync as running when the process running it is gone.
    repo.reap_expired()
    job = repo.latest(JOB_TYPE)
    return as_dict(repo, job) if job else None


def start_sync_job(db_path) -> tuple[dict, bool]:
    """(job, created). A live job is returned as-is: clicking Sync twice
    can never run two syncs at once, and now that holds across a restart
    as well as within one process."""
    from leaguepage.job_runner import start_in_thread

    repo = _repo(db_path)
    job, created = repo.create(jobs_mod.JobSpec(
        job_type=JOB_TYPE, scope=jobs_mod.SCOPE_GLOBAL,
        idempotency_key=IDEMPOTENCY_KEY, stages=_stages()))
    if created:
        start_in_thread(repo, job.job_id, STAGE_FNS, db_path=db_path)
    return as_dict(repo, job), created


# ------------------------------------------------------------------ stages

def _timing(ctx: JobContext, key: str, seconds: float) -> None:
    """Per-step timings recorded on the job. The roadmap forbids letting the
    added analytics make Sync feel slow, so the cost is measured and shown
    on the Desk rather than assumed to be small."""
    ctx.result.setdefault("timings", {})[key] = round(seconds, 3)


def _tx_count(s: Storage, league) -> int:
    return sum(len(s.get_transactions(league.league_id, wk)) for wk in range(0, 19))


def _sync_results(ctx: JobContext) -> list:
    """Run the Sleeper sync once, whichever league's stage asks first.

    `sync_all` fetches the player dictionary and the current week before
    looping the leagues, so splitting it per league would double that work
    for no gain. The per-league stages stay because a failure belongs to a
    league, not to "the sync".
    """
    if "results" in ctx.scratch:
        return ctx.scratch["results"]
    from leaguepage.ingest import sync_all
    from leaguepage.team_names import sleeper_team_names

    with Storage(ctx.db_path) as s:
        before_names, before_tx = {}, {}
        for lg in LEAGUES:
            try:
                before_names[lg.slug] = dict(sleeper_team_names(s, lg))
            except Exception:                                   # noqa: BLE001
                before_names[lg.slug] = {}
            before_tx[lg.slug] = _tx_count(s, lg)
        results = sync_all(s, weeks_back=1)
        ctx.scratch.update(results=results, before_names=before_names,
                           before_tx=before_tx,
                           week=s.get_meta("current_week"))
    return results


def _stage_sleeper(slug: str):
    def run(ctx: JobContext) -> str:
        result = next((r for r in _sync_results(ctx) if r.league.slug == slug), None)
        if result is None:
            raise SoftStageError(f"{slug} was not part of this sync",
                                 code="league_missing")
        if not result.ok:
            # Soft: the other league's data, if it synced, is kept, and its
            # context and research still get computed.
            raise SoftStageError(
                f"{result.league.display_name} did not sync: "
                f"{result.error or 'unknown error'}. The other league's data "
                "(if it synced) is kept.",
                code="league_sync_failed",
                detail=result.error or "unknown error")
        return (f"{result.rosters} teams, {result.picks} picks, "
                f"weeks {result.weeks_synced or '—'}")
    return run


def _ok_results(ctx: JobContext) -> list:
    return [r for r in _sync_results(ctx) if r.ok]


def _stage_context(ctx: JobContext) -> str:
    """Snapshots and transaction context: the same steps scripts/sync.py runs."""
    from leaguepage import change_inbox
    from leaguepage import takes as takes_mod
    from leaguepage.matchup_analysis import weekly_scores
    from leaguepage.team_analytics import get_snapshot, record_snapshot
    from leaguepage.transaction_analysis import record_transaction_contexts

    results = _ok_results(ctx)
    if not results:
        raise SkipStage("no league synced")
    week = ctx.scratch.get("week")
    bits: list[str] = []
    with Storage(ctx.db_path) as s:
        for r in results:
            data = s.get_league(r.league.league_id) or {}
            season = str(data.get("season") or "")
            if season:
                if not get_snapshot(s, r.league, season, 0):
                    record_snapshot(s, r.league, season, 0)
                scores = weekly_scores(s, r.league.league_id, int(week or 1))
                played = max((len(v) for v in scores.values()), default=0)
                if played:
                    record_snapshot(s, r.league, season, played)
                # Change Inbox baseline. Stored per SYNC rather than per week,
                # and skipped when nothing moved, so pressing Sync twice does
                # not blank the inbox. Timed because the roadmap forbids
                # making Sync feel slow.
                t0 = time.monotonic()
                snap = change_inbox.record(s, r.league, season, int(week or 1))
                _timing(ctx, f"change_snapshot:{r.league.slug}", time.monotonic() - t0)
                if snap:
                    bits.append(f"{r.league.slug}: change snapshot recorded")
            stored = record_transaction_contexts(s, r.league)
            if stored:
                bits.append(f"{r.league.slug}: {stored} new move context(s)")
            # Re-read every open take against the new state. Persisted here so
            # the public build and the Desk both read a stored result rather
            # than recomputing.
            t0 = time.monotonic()
            evaluated = takes_mod.evaluate_all(s, r.league, season, int(week or 1))
            _timing(ctx, f"takes_eval:{r.league.slug}", time.monotonic() - t0)
            moved = [e for e in evaluated
                     if e["recommended_status"] != (e.get("status") or "open")]
            if moved:
                bits.append(f"{r.league.slug}: {len(moved)} receipt(s) ready")
    ctx.save()
    return "; ".join(bits) or "up to date"


def _stage_editorial(ctx: JobContext) -> str:
    """Refresh research for existing current-week workspaces, then record
    the per-league summary the Desk shows and stamp the sync time."""
    from leaguepage.desk import refresh_issue_research
    from leaguepage.issue_builder import issue_dir
    from leaguepage.team_names import sleeper_team_names

    results = _ok_results(ctx)
    if not results:
        raise SkipStage("no league synced")
    week = ctx.scratch.get("week")
    before_names = ctx.scratch.get("before_names", {})
    before_tx = ctx.scratch.get("before_tx", {})
    refreshed: list[str] = []
    with Storage(ctx.db_path) as s:
        for r in results:
            data = s.get_league(r.league.league_id) or {}
            season = str(data.get("season") or "")
            if not season:
                continue
            issue_key = f"week-{int(week or 1):02d}"
            exists = bool(s.get_issue(r.league.slug, season, issue_key)) or \
                issue_dir(r.league, season, issue_key).exists()
            if exists:
                t0 = time.monotonic()
                refresh_issue_research(s, r.league, season, issue_key)
                _timing(ctx, f"issue_refresh:{r.league.slug}", time.monotonic() - t0)
                refreshed.append(f"{r.league.slug} {issue_key}")

        # The per-league result panel, including the leagues that failed.
        summary = []
        for r in _sync_results(ctx):
            if not r.ok:
                summary.append({"league": r.league.display_name, "ok": False,
                                "error": r.error})
                continue
            try:
                after = dict(sleeper_team_names(s, r.league))
            except Exception:                                   # noqa: BLE001
                after = {}
            prior = before_names.get(r.league.slug) or {}
            renames = [f"{prior.get(rid) or f'Roster {rid}'} → {name}"
                       for rid, name in after.items()
                       if name and prior.get(rid) != name and prior]
            new_tx = _tx_count(s, r.league) - before_tx.get(r.league.slug, 0)
            summary.append({
                "league": r.league.display_name, "ok": True, "week": week,
                "teams": r.rosters, "new_transactions": max(0, new_tx),
                "renames": renames[:6], "warnings": r.warnings[:4]})
        ctx.result["summary"] = summary
        s.set_meta(LAST_SYNC_KEY, _now())
    ctx.save()
    return ("refreshed " + ", ".join(refreshed)) if refreshed \
        else "no current-week workspace yet (created on first Edit)"


STAGE_FNS = {f"sleeper:{lg.slug}": _stage_sleeper(lg.slug) for lg in LEAGUES}
STAGE_FNS["context"] = _stage_context
STAGE_FNS["editorial"] = _stage_editorial
