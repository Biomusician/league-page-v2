"""Executing a job, as distinct from recording one.

`jobs` owns the record: what was asked for, what has happened, who holds
the lease. This module owns the doing. The split is the point. A job can
be created by a request handler that then returns, executed by a worker
that is not the process which created it, and read by a browser that
knows about neither. Today all three happen to be the same Python
process; nothing here depends on that, which is what makes the eventual
move to a queue and a separate worker a change of caller rather than a
rewrite.

Two rules hold the whole thing together:

* **The lease is permission to write.** A worker heartbeats on its own
  thread while a stage runs, so a six-minute deploy keeps a ninety-second
  lease alive, and a process that dies stops renewing within ninety
  seconds rather than looking healthy until its longest timeout. If a
  heartbeat is refused the worker has been superseded, and it stops
  writing immediately rather than racing its own replacement.
* **A stage that already succeeded is never run again.** Freezing a
  snapshot and deploying to production are not repeatable, so resumption
  skips what the event log says is done. Nothing here resumes on its own:
  recovering a lost job means claiming it again, and only a person does
  that, having read what `job_recovery` found.
"""
from __future__ import annotations

import os
import socket
import threading
import traceback
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from leaguepage.jobs import (DEFAULT_LEASE_SECONDS, FAILED, OK, PENDING,
                             SKIPPED, STAGE_FAILED, STAGE_RUNNING, SUCCEEDED,
                             Job, JobRepository, fold_stages)

HEARTBEAT_SECONDS = 30


class LeaseLost(Exception):
    """This worker is no longer the one executing this job."""


class StageError(Exception):
    """A stage refused or failed.

    `message` is what the Commissioner reads and what the durable record
    keeps: a sentence about his issue, not a stack trace. `code` is the
    stable slug a screen or a test can branch on without matching prose.
    Everything bulkier than that (subprocess tails, absolute paths,
    tracebacks) goes to the job's log file, which is where a diagnosis
    belongs and where it does not become part of the record.
    """

    def __init__(self, message: str, *, code: str = "stage_failed",
                 detail: str = ""):
        super().__init__(message)
        self.message = message
        self.code = code
        self.detail = detail or message


class SoftStageError(StageError):
    """This stage failed, and the rest of the job should still run.

    One league failing to sync does not make the other league's data
    worthless, and the existing product says so: the working league is
    kept, its context is still computed, and the job as a whole reports
    failure. Aborting here would throw away work that succeeded.
    """


class SkipStage(Exception):
    """This stage had nothing to do, and that is not a failure.

    Distinct from success because "skipped" and "ok" mean different things
    to somebody reading the panel: no research was refreshed because no
    league synced is not the same as no research needed refreshing.
    """

    def __init__(self, reason: str = ""):
        super().__init__(reason)
        self.reason = reason


def worker_id() -> str:
    """Who holds the lease. Host and pid make a lost lease traceable to a
    machine and a process; the suffix keeps two workers in one process
    distinct."""
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


@dataclass
class JobContext:
    """What a stage function is given.

    A stage gets the durable job, a way to record what it learned, and a
    log. It does not get a database cursor or the repository's internals,
    so a stage cannot quietly write around the lease.
    """
    job: Job
    repo: JobRepository
    owner: str
    db_path: Path
    log: Callable[[str], None]
    result: dict = field(default_factory=dict)
    # Cross-stage working state that is deliberately not durable: API
    # results, timers, objects that do not survive JSON. A resumed job
    # re-derives it, which is safe precisely because nothing irreversible
    # is ever kept here.
    scratch: dict = field(default_factory=dict)

    def save(self) -> None:
        """Persist `result` now, rather than at the end. A job that dies
        mid-stage still shows what it had established."""
        if not self.repo.set_result(self.job.job_id, self.owner, self.result):
            raise LeaseLost(self.job.job_id)

    def bind_revision(self, revision: int) -> None:
        """Pin this job to the immutable revision it just froze.

        Refused if the job is already bound to a different one, which is
        how a resumed publish is stopped from switching targets between
        the snapshot it took and the deploy it ships.
        """
        if not self.repo.set_target_revision(self.job.job_id, self.owner, revision):
            raise StageError(
                "this job is already bound to a different revision of the issue; "
                "it will not switch targets mid-publish",
                code="target_revision_conflict")
        self.job.target_revision = revision

    def checkpoint(self, name: str, before: str = "", after: str = "") -> None:
        """Record what the world looked like either side of something that
        cannot be undone, so a later reader can ask whether it happened."""
        marks = self.result.setdefault("checkpoints", {})
        mark = marks.setdefault(name, {})
        if before:
            mark["before"] = before
        if after:
            mark["after"] = after
        self.save()


StageFn = Callable[[JobContext], str]


def _heartbeat_thread(repo: JobRepository, job_id: str, owner: str,
                      lease_seconds: int, stop: threading.Event,
                      lost: threading.Event) -> threading.Thread:
    def beat() -> None:
        while not stop.wait(HEARTBEAT_SECONDS):
            try:
                if not repo.heartbeat(job_id, owner, lease_seconds):
                    lost.set()
                    return
            except Exception:                                  # noqa: BLE001
                # A transient database error is not proof the lease is
                # gone; the next beat decides, and the lease outlives one
                # missed beat by design.
                continue
    t = threading.Thread(target=beat, name=f"job-heartbeat-{job_id[:8]}",
                         daemon=True)
    t.start()
    return t


def run_job(repo: JobRepository, job_id: str, stage_fns: dict[str, StageFn], *,
            db_path: Path, owner: str | None = None,
            lease_seconds: int = DEFAULT_LEASE_SECONDS,
            log: Callable[[str], None] | None = None,
            on_finish: Callable[[JobContext, str], None] | None = None) -> str | None:
    """Claim a job, run the stages it has not already completed, end it.

    Returns the terminal state, or None when the job could not be claimed
    (somebody else holds a live lease on it). Never raises for ordinary
    failure: a job that fails is a recorded fact, not an exception for a
    caller who has usually already returned an HTTP response.
    """
    owner = owner or worker_id()
    log = log or (lambda _line: None)
    if not repo.claim(job_id, owner, lease_seconds):
        return None
    job = repo.get(job_id)
    if job is None:
        return None
    ctx = JobContext(job=job, repo=repo, owner=owner, db_path=Path(db_path),
                     log=log, result=dict(job.result))
    stop, lost = threading.Event(), threading.Event()
    beat = _heartbeat_thread(repo, job_id, owner, lease_seconds, stop, lost)

    state, error, code = SUCCEEDED, None, None
    aborted = False
    try:
        for stage in fold_stages(repo.events(job_id)):
            key, name = stage["key"], stage["name"]
            if stage["status"] == OK:
                # Already done, by this worker or a previous one. Re-running
                # it would freeze a second snapshot or ship a second deploy.
                log(f"STAGE {key} already complete; not run again")
                continue
            if lost.is_set():
                raise LeaseLost(job_id)
            _event(repo, job_id, owner, key, STAGE_RUNNING, "", name)
            log(f"STAGE {key} start")
            try:
                detail = stage_fns[key](ctx) or ""
            except SkipStage as exc:
                _event(repo, job_id, owner, key, SKIPPED, exc.reason, name)
                log(f"STAGE {key} skipped: {exc.reason}")
                continue
            except SoftStageError as exc:
                # Recorded, not fatal. The job ends failed, but the stages
                # after this one still run, because their work is still
                # worth having.
                log(f"FAIL(soft) {key}: {exc.detail}")
                _event(repo, job_id, owner, key, STAGE_FAILED, exc.message, name)
                if state != FAILED:
                    state, error, code = FAILED, exc.message, exc.code
                continue
            except StageError as exc:
                log(f"FAIL {key}: {exc.detail}")
                _event(repo, job_id, owner, key, STAGE_FAILED, exc.message, name)
                state, error, code = FAILED, exc.message, exc.code
                aborted = True
                break
            except LeaseLost:
                raise
            except Exception as exc:                            # noqa: BLE001
                log(f"FAIL {key}: {traceback.format_exc()}")
                message = f"{type(exc).__name__}: {exc}"
                _event(repo, job_id, owner, key, STAGE_FAILED, message, name)
                state, error, code = FAILED, message, "stage_crashed"
                break
            _event(repo, job_id, owner, key, OK, detail, name)
            log(f"STAGE {key} ok: {detail}")
        else:
            log("all stages complete")
        # Stages after a hard failure never ran, and the record says so
        # rather than leaving them looking pending forever.
        if aborted:
            _skip_remaining(repo, job_id, owner)
    except LeaseLost:
        # Somebody else owns this job now. Writing anything further would
        # overwrite their record of it.
        log("LEASE LOST; this worker stopped writing")
        stop.set()
        beat.join(timeout=2)
        return None
    finally:
        stop.set()
        beat.join(timeout=2)

    if on_finish is not None:
        try:
            on_finish(ctx, state)
        except Exception:                                       # noqa: BLE001
            log(f"on_finish failed: {traceback.format_exc()}")
    repo.finish(job_id, owner, state, error=error, error_code=code,
                result=ctx.result)
    log(f"---- job {state} ----")
    return state


def _event(repo: JobRepository, job_id: str, owner: str, key: str,
           status: str, detail: str, name: str) -> None:
    if not repo.append_event(job_id, owner, key, status, detail, name):
        raise LeaseLost(job_id)


def _skip_remaining(repo: JobRepository, job_id: str, owner: str) -> None:
    for stage in fold_stages(repo.events(job_id)):
        if stage["status"] == PENDING:
            repo.append_event(job_id, owner, stage["key"], SKIPPED,
                              "not reached", stage["name"])


def start_in_thread(repo: JobRepository, job_id: str,
                    stage_fns: dict[str, StageFn], *, db_path: Path,
                    **kw) -> threading.Thread:
    """The local worker: one daemon thread per job, on this machine.

    This is the only place the Desk starts background execution, and the
    only thing that has to change when execution moves to a queue. The
    thread is still a daemon, so a killed process still abandons it, but
    now abandonment is visible: the lease stops being renewed and the job
    turns up as lost rather than as a spinner that never resolves.
    """
    t = threading.Thread(
        target=run_job, args=(repo, job_id, stage_fns),
        kwargs={"db_path": db_path, **kw},
        name=f"job-{job_id[:8]}", daemon=True)
    t.start()
    return t
