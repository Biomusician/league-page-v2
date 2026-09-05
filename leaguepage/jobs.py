"""Durable job records for the Commissioner's Desk.

The Desk used to keep a job in a module global and a daemon thread, which
answers "what is happening right now" only while the process that started
it is still alive. A `uvicorn --reload` restart, a crash, or a laptop lid
took the answer with it, and the deploy it was running kept going. The
Commissioner was then left with a production site that may or may not
carry his correction, and nothing on the Desk that could say which.

So a job is a row, not an object. The row records what was requested,
which immutable thing it applies to, who is executing it now, what has
already happened, and how it ended. A process may vanish between any two
statements here; nothing in this module assumes it did not.

Three things make that work:

* **Leases.** A worker does not "have" a job, it holds a lease on one for
  a bounded time and renews it. Every write is guarded by the lease, so a
  worker that has been declared dead cannot come back and overwrite the
  record of its own replacement. `claim` is one UPDATE with the guard in
  its WHERE clause, so two workers racing for the same job cannot both
  win, whatever the isolation level underneath.
* **An append-only event log.** Stages are not a JSON blob rewritten in
  place; they are events. The current stage list is a fold over them, and
  the history that fold discards is exactly what a recovery needs.
* **Idempotency keys held only while a job is live.** A second Sync click
  joins the running sync instead of starting a second one, and the key is
  released when the job ends, so tomorrow's sync is not blocked by
  today's. Publishing is keyed per issue for the same reason.

This module is the control plane. It knows nothing about Sleeper, Vercel,
snapshots or prose, and it starts nothing: `job_runner` executes, and the
job type modules supply the stages. Keeping that seam is what lets the
same records be served later by a Postgres table and a worker that is not
this process.

`JobRepository` is deliberately small, and free of SQL, datetimes and
connections in its signatures, because a `PostgresJobRepository` has to be
able to implement it exactly. Nothing above this module may reach past it
to the database.
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Protocol

# ---------------------------------------------------------------- vocabulary

# A job is queued, running, or over. "lost" is over as well: the worker
# stopped renewing its lease and nobody can say whether the work finished.
# It is kept distinct from "failed" because a failure means the work did
# not happen and a loss means nobody knows, and those ask different things
# of a person.
QUEUED, RUNNING, SUCCEEDED, FAILED, LOST = (
    "queued", "running", "succeeded", "failed", "lost")
ACTIVE_STATES = (QUEUED, RUNNING)
TERMINAL_STATES = (SUCCEEDED, FAILED, LOST)

# One stage vocabulary for every job type. The two job modules had
# disagreed (one said "fail", the other "failed") and the browser rendered
# the odd one out as a bullet instead of a cross.
PENDING, STAGE_RUNNING, OK, STAGE_FAILED, SKIPPED = (
    "pending", "running", "ok", "failed", "skipped")

# What a job is about. "global" work has no single target; "issue" work is
# bound to one immutable issue and, once frozen, to one revision of it.
SCOPE_GLOBAL, SCOPE_ISSUE = "global", "issue"

DEFAULT_LEASE_SECONDS = 90


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso(when: dt.datetime) -> str:
    return when.isoformat(timespec="seconds")


def new_job_id() -> str:
    """32 hex digits: a uuid4 that a Postgres `uuid` column accepts as-is."""
    return uuid.uuid4().hex


# --------------------------------------------------------------- the records

@dataclass
class Job:
    job_id: str
    job_type: str
    scope: str
    state: str
    created_at: str
    updated_at: str
    league_slug: str | None = None
    season: str | None = None
    issue_key: str | None = None
    mode: str | None = None
    # The immutable thing this job acts on. For a publish it is the
    # revision the snapshot stage froze; every later stage uses this and
    # never re-reads the directory, so a job cannot start shipping r2 and
    # finish shipping r3.
    target_revision: int | None = None
    idempotency_key: str | None = None
    request: dict = field(default_factory=dict)
    result: dict = field(default_factory=dict)
    error: str | None = None
    error_code: str | None = None
    lease_owner: str | None = None
    lease_expires_at: str | None = None
    heartbeat_at: str | None = None
    attempts: int = 0
    started_at: str | None = None
    ended_at: str | None = None

    @property
    def active(self) -> bool:
        """Whether the Desk should still be watching this one. The browser
        asks this rather than `state == "running"`, so a job that exists
        but has not been claimed yet still disables its button."""
        return self.state in ACTIVE_STATES

    @property
    def target(self) -> str | None:
        if self.scope != SCOPE_ISSUE:
            return None
        return f"{self.league_slug}:{self.season}:{self.issue_key}"


@dataclass
class JobEvent:
    seq: int
    stage_key: str
    stage_name: str
    status: str
    detail: str
    at: str


@dataclass
class JobSpec:
    """What the Desk asks for, before a row exists."""
    job_type: str
    scope: str = SCOPE_GLOBAL
    league_slug: str | None = None
    season: str | None = None
    issue_key: str | None = None
    mode: str | None = None
    request: dict = field(default_factory=dict)
    # None means "no deduplication": every request gets its own job.
    idempotency_key: str | None = None
    stages: list[tuple[str, str]] = field(default_factory=list)


def fold_stages(events: list[JobEvent]) -> list[dict]:
    """The stage list the Desk renders, folded from the event log.

    Declaration order is event order, because a job's stages are written
    as `pending` when it is created. Later events overwrite status and
    detail in place, so the fold is last-write-wins per stage, and the
    history it discards stays in the log for a recovery to read.
    """
    out: dict[str, dict] = {}
    for e in events:
        cur = out.get(e.stage_key)
        if cur is None:
            out[e.stage_key] = {"key": e.stage_key, "name": e.stage_name,
                                "status": e.status, "detail": e.detail}
            continue
        cur["status"] = e.status
        cur["detail"] = e.detail
        if e.stage_name:
            cur["name"] = e.stage_name
    return list(out.values())


# ------------------------------------------------------------ the contract

class JobRepository(Protocol):
    """Every durable operation the Desk performs on a job.

    Narrow on purpose. No SQL, no connection, no cursor and no datetime
    object crosses this line, so the SQLite implementation below and a
    Postgres one later are interchangeable without anything above them
    noticing. Methods that can lose a race return a bool rather than
    raising, because losing a race is ordinary here, not exceptional.
    """

    def create(self, spec: JobSpec) -> tuple[Job, bool]:
        """(job, created). A live job with the same idempotency key is
        returned as-is, with created=False."""

    def get(self, job_id: str) -> Job | None: ...

    def latest(self, job_type: str, *, league_slug: str | None = None,
               season: str | None = None,
               issue_key: str | None = None) -> Job | None:
        """Most recent job for a target, running or finished: what the
        progress panel shows, and what a restart reads to catch up."""

    def active(self, job_type: str | None = None) -> list[Job]: ...

    def events(self, job_id: str) -> list[JobEvent]: ...

    def claim(self, job_id: str, owner: str, lease_seconds: int) -> bool:
        """Take the lease. False means somebody else holds it."""

    def heartbeat(self, job_id: str, owner: str, lease_seconds: int) -> bool:
        """Extend the lease. False means it was taken away; the caller has
        become a stale worker and must stop writing."""

    def append_event(self, job_id: str, owner: str, stage_key: str,
                     status: str, detail: str = "",
                     stage_name: str = "") -> bool: ...

    def set_target_revision(self, job_id: str, owner: str,
                            revision: int) -> bool:
        """Bind the job to an immutable revision. Binding twice to the same
        number is fine; rebinding to a different one is refused."""

    def set_result(self, job_id: str, owner: str, result: dict) -> bool: ...

    def finish(self, job_id: str, owner: str, state: str, *,
               error: str | None = None, error_code: str | None = None,
               result: dict | None = None) -> bool: ...

    def reap_expired(self, *, now: str | None = None) -> list[Job]:
        """Mark running jobs whose lease has expired as lost, and return
        them. It never decides what their side effects did."""

    def purge(self, *, keep_days: int, keep_per_type: int) -> int: ...


# --------------------------------------------------------------------- DDL

# Kept here rather than inside storage.SCHEMA so the repository can
# guarantee its own tables when used outside the Desk, and storage can
# apply the same text on startup. One definition, two callers.
JOBS_DDL = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id           TEXT PRIMARY KEY,
    job_type         TEXT NOT NULL,          -- sync | publish
    scope            TEXT NOT NULL,          -- global | issue
    league_slug      TEXT,
    season           TEXT,
    issue_key        TEXT,
    mode             TEXT,                   -- publish: local | deploy
    target_revision  INTEGER,                -- the immutable revision, once bound
    idempotency_key  TEXT,                   -- held only while the job is live
    request          TEXT NOT NULL DEFAULT '{}',
    result           TEXT NOT NULL DEFAULT '{}',
    state            TEXT NOT NULL DEFAULT 'queued',
    error            TEXT,
    error_code       TEXT,                   -- stable; `error` is prose
    lease_owner      TEXT,
    lease_expires_at TEXT,
    heartbeat_at     TEXT,
    attempts         INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    started_at       TEXT,
    ended_at         TEXT
);
-- Partial, so finished jobs (whose key is released) cannot collide.
CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_idempotency
    ON jobs(idempotency_key) WHERE idempotency_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_jobs_recent
    ON jobs(job_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_target
    ON jobs(league_slug, season, issue_key, created_at DESC);
-- The reaper's query, and the only one that runs on a timer.
CREATE INDEX IF NOT EXISTS idx_jobs_lease
    ON jobs(state, lease_expires_at);

CREATE TABLE IF NOT EXISTS job_events (
    event_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id     TEXT NOT NULL,
    seq        INTEGER NOT NULL,
    stage_key  TEXT NOT NULL,
    stage_name TEXT NOT NULL DEFAULT '',
    status     TEXT NOT NULL,
    detail     TEXT NOT NULL DEFAULT '',
    at         TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_job_events_seq ON job_events(job_id, seq);
"""

_COLUMNS = ("job_id", "job_type", "scope", "league_slug", "season", "issue_key",
            "mode", "target_revision", "idempotency_key", "request", "result",
            "state", "error", "error_code", "lease_owner", "lease_expires_at",
            "heartbeat_at", "attempts", "created_at", "updated_at",
            "started_at", "ended_at")

_ENSURED: set[str] = set()


def _loads(raw) -> dict:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _job(row: sqlite3.Row) -> Job:
    data = {k: row[k] for k in _COLUMNS}
    data["request"] = _loads(data["request"])
    data["result"] = _loads(data["result"])
    return Job(**data)


class SQLiteJobRepository:
    """The local implementation. One connection per operation, matching the
    rest of the app: request handlers and job threads each open their own,
    so `check_same_thread` is never violated and no connection is shared.

    Writes run inside an explicit `BEGIN IMMEDIATE`. SQLite's default
    deferred transaction upgrades a read to a write only when the write
    arrives, which is exactly when two threads doing read-then-write can
    collide; taking the write lock up front removes that window. Reads run
    outside a transaction, as they do everywhere else here.
    """

    def __init__(self, db_path: Path | str, *, timeout: float = 15.0):
        self.db_path = Path(db_path)
        self.timeout = timeout
        self._ensure()

    # ------------------------------------------------------ connections

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, isolation_level=None,
                               timeout=self.timeout)
        conn.row_factory = sqlite3.Row
        # Per-connection, so it changes no file and no other component's
        # behaviour. Without it, a job thread and a request thread writing
        # at once give up after the 5s default and surface as a crash.
        conn.execute(f"PRAGMA busy_timeout = {int(self.timeout * 1000)}")
        return conn

    def _ensure(self) -> None:
        key = str(self.db_path.resolve())
        if key in _ENSURED:
            return
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = self._connect()
        try:
            conn.executescript(JOBS_DDL)
        finally:
            conn.close()
        _ENSURED.add(key)

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except Exception:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise
            conn.execute("COMMIT")
        finally:
            conn.close()

    # ----------------------------------------------------------- create

    def create(self, spec: JobSpec) -> tuple[Job, bool]:
        now = iso(utcnow())
        with self._write() as conn:
            if spec.idempotency_key:
                row = conn.execute(
                    "SELECT * FROM jobs WHERE idempotency_key = ?",
                    (spec.idempotency_key,)).fetchone()
                if row is not None:
                    return _job(row), False
            job = Job(job_id=new_job_id(), job_type=spec.job_type,
                      scope=spec.scope, state=QUEUED, created_at=now,
                      updated_at=now, league_slug=spec.league_slug,
                      season=spec.season, issue_key=spec.issue_key,
                      mode=spec.mode, idempotency_key=spec.idempotency_key,
                      request=dict(spec.request))
            conn.execute(
                f"INSERT INTO jobs ({', '.join(_COLUMNS)}) "
                f"VALUES ({', '.join('?' * len(_COLUMNS))})",
                (job.job_id, job.job_type, job.scope, job.league_slug,
                 job.season, job.issue_key, job.mode, None,
                 job.idempotency_key, json.dumps(job.request), "{}",
                 job.state, None, None, None, None, None, 0, now, now,
                 None, None))
            # The plan, written as pending events in one transaction with
            # the job itself. Declaring stages up front is what lets the
            # Desk show the whole pipeline before any of it has run, and
            # what fixes their order for the fold.
            for seq, (key, name) in enumerate(spec.stages):
                conn.execute(
                    "INSERT INTO job_events (job_id, seq, stage_key, stage_name, "
                    "status, detail, at) VALUES (?, ?, ?, ?, ?, '', ?)",
                    (job.job_id, seq, key, name, PENDING, now))
        return job, True

    # ------------------------------------------------------------ reads

    def get(self, job_id: str) -> Job | None:
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM jobs WHERE job_id = ?",
                               (job_id,)).fetchone()
        finally:
            conn.close()
        return _job(row) if row else None

    def latest(self, job_type: str, *, league_slug: str | None = None,
               season: str | None = None,
               issue_key: str | None = None) -> Job | None:
        where = ["job_type = ?"]
        args: list = [job_type]
        for col, val in (("league_slug", league_slug), ("season", season),
                         ("issue_key", issue_key)):
            if val is not None:
                where.append(f"{col} = ?")
                args.append(val)
        conn = self._connect()
        try:
            row = conn.execute(
                f"SELECT * FROM jobs WHERE {' AND '.join(where)} "
                "ORDER BY created_at DESC, rowid DESC LIMIT 1", args).fetchone()
        finally:
            conn.close()
        return _job(row) if row else None

    def active(self, job_type: str | None = None) -> list[Job]:
        sql = ("SELECT * FROM jobs WHERE state IN (?, ?)"
               + (" AND job_type = ?" if job_type else "")
               + " ORDER BY created_at")
        args = [QUEUED, RUNNING] + ([job_type] if job_type else [])
        conn = self._connect()
        try:
            return [_job(r) for r in conn.execute(sql, args).fetchall()]
        finally:
            conn.close()

    def events(self, job_id: str) -> list[JobEvent]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT seq, stage_key, stage_name, status, detail, at "
                "FROM job_events WHERE job_id = ? ORDER BY seq",
                (job_id,)).fetchall()
        finally:
            conn.close()
        return [JobEvent(seq=r["seq"], stage_key=r["stage_key"],
                         stage_name=r["stage_name"], status=r["status"],
                         detail=r["detail"], at=r["at"]) for r in rows]

    # ----------------------------------------------------------- leases

    def claim(self, job_id: str, owner: str, lease_seconds: int) -> bool:
        """One statement, so the guard and the write cannot be separated.

        A job is claimable when it is queued, or when it is running behind
        a lease that has expired. The second case is how work is recovered
        after a worker dies; the previous holder's writes stop being
        accepted the moment this succeeds, because every other write here
        checks `lease_owner`.
        """
        now = utcnow()
        with self._write() as conn:
            cur = conn.execute(
                "UPDATE jobs SET state = ?, lease_owner = ?, lease_expires_at = ?, "
                "heartbeat_at = ?, started_at = COALESCE(started_at, ?), "
                "attempts = attempts + 1, updated_at = ? "
                "WHERE job_id = ? AND (state = ? OR (state = ? AND "
                "  lease_expires_at IS NOT NULL AND lease_expires_at < ?))",
                (RUNNING, owner,
                 iso(now + dt.timedelta(seconds=lease_seconds)), iso(now),
                 iso(now), iso(now), job_id, QUEUED, RUNNING, iso(now)))
            return cur.rowcount == 1

    def heartbeat(self, job_id: str, owner: str, lease_seconds: int) -> bool:
        now = utcnow()
        with self._write() as conn:
            cur = conn.execute(
                "UPDATE jobs SET lease_expires_at = ?, heartbeat_at = ?, "
                "updated_at = ? WHERE job_id = ? AND lease_owner = ? AND state = ?",
                (iso(now + dt.timedelta(seconds=lease_seconds)), iso(now),
                 iso(now), job_id, owner, RUNNING))
            return cur.rowcount == 1

    # ------------------------------------------------------------ writes

    def append_event(self, job_id: str, owner: str, stage_key: str,
                     status: str, detail: str = "", stage_name: str = "") -> bool:
        now = iso(utcnow())
        with self._write() as conn:
            if not self._owns(conn, job_id, owner):
                return False
            seq = conn.execute(
                "SELECT COALESCE(MAX(seq), -1) + 1 FROM job_events WHERE job_id = ?",
                (job_id,)).fetchone()[0]
            conn.execute(
                "INSERT INTO job_events (job_id, seq, stage_key, stage_name, "
                "status, detail, at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (job_id, seq, stage_key, stage_name, status, detail, now))
            conn.execute("UPDATE jobs SET updated_at = ? WHERE job_id = ?",
                         (now, job_id))
            return True

    def set_target_revision(self, job_id: str, owner: str, revision: int) -> bool:
        """Binding is once and for all.

        A publish that froze r2 must ship r2. If the job were allowed to
        re-read the directory later it could deploy, verify and record a
        revision nobody asked for, which is the failure this whole tranche
        exists to make impossible.
        """
        now = iso(utcnow())
        with self._write() as conn:
            row = conn.execute(
                "SELECT target_revision FROM jobs "
                "WHERE job_id = ? AND lease_owner = ?", (job_id, owner)).fetchone()
            if row is None:
                return False
            if row["target_revision"] is not None:
                return int(row["target_revision"]) == int(revision)
            conn.execute(
                "UPDATE jobs SET target_revision = ?, updated_at = ? WHERE job_id = ?",
                (int(revision), now, job_id))
            return True

    def set_result(self, job_id: str, owner: str, result: dict) -> bool:
        now = iso(utcnow())
        with self._write() as conn:
            cur = conn.execute(
                "UPDATE jobs SET result = ?, updated_at = ? "
                "WHERE job_id = ? AND lease_owner = ?",
                (json.dumps(result), now, job_id, owner))
            return cur.rowcount == 1

    def finish(self, job_id: str, owner: str, state: str, *,
               error: str | None = None, error_code: str | None = None,
               result: dict | None = None) -> bool:
        """End the job and release its idempotency key, so the next Sync,
        or the next publish of this issue, is not blocked by a finished
        one."""
        now = iso(utcnow())
        sets = ["state = ?", "error = ?", "error_code = ?", "ended_at = ?",
                "updated_at = ?", "idempotency_key = NULL",
                "lease_owner = NULL", "lease_expires_at = NULL"]
        args: list = [state, error, error_code, now, now]
        if result is not None:
            sets.insert(3, "result = ?")
            args.insert(3, json.dumps(result))
        args += [job_id, owner]
        with self._write() as conn:
            cur = conn.execute(
                f"UPDATE jobs SET {', '.join(sets)} "
                "WHERE job_id = ? AND lease_owner = ?", args)
            return cur.rowcount == 1

    # -------------------------------------------------------- housekeeping

    def reap_expired(self, *, now: str | None = None) -> list[Job]:
        """Declare dead what has stopped renewing.

        It says only that nobody is executing the job any more. Whether
        the deploy it was running reached production is not a question a
        timer can answer, so this never touches side effects and never
        retries; `job_recovery` asks the world instead.
        """
        stamp = now or iso(utcnow())
        with self._write() as conn:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE state = ? AND lease_expires_at IS NOT NULL "
                "AND lease_expires_at < ?", (RUNNING, stamp)).fetchall()
            lost = [_job(r) for r in rows]
            for job in lost:
                conn.execute(
                    "UPDATE jobs SET state = ?, error = ?, error_code = ?, "
                    "ended_at = ?, updated_at = ?, idempotency_key = NULL, "
                    "lease_owner = NULL, lease_expires_at = NULL WHERE job_id = ?",
                    (LOST,
                     "the worker running this job stopped reporting; what it had "
                     "already started may or may not have finished",
                     "lease_expired", stamp, stamp, job.job_id))
        return lost

    def purge(self, *, keep_days: int = 30, keep_per_type: int = 50) -> int:
        """Drop finished jobs that are both old and not among the recent
        ones for their type, with their events. Live jobs are never
        touched, and the most recent jobs per type always survive, so the
        Desk's progress panel cannot go blank."""
        cutoff = iso(utcnow() - dt.timedelta(days=keep_days))
        with self._write() as conn:
            keep = {r["job_id"] for r in conn.execute(
                "SELECT job_id FROM ("
                "  SELECT job_id, ROW_NUMBER() OVER ("
                "    PARTITION BY job_type ORDER BY created_at DESC, rowid DESC"
                "  ) AS rn FROM jobs) WHERE rn <= ?", (keep_per_type,)).fetchall()}
            doomed = [r["job_id"] for r in conn.execute(
                "SELECT job_id FROM jobs WHERE state IN (?, ?, ?) AND created_at < ?",
                (SUCCEEDED, FAILED, LOST, cutoff)).fetchall()
                if r["job_id"] not in keep]
            for job_id in doomed:
                conn.execute("DELETE FROM job_events WHERE job_id = ?", (job_id,))
                conn.execute("DELETE FROM jobs WHERE job_id = ?", (job_id,))
        return len(doomed)

    # --------------------------------------------------------------- util

    @staticmethod
    def _owns(conn: sqlite3.Connection, job_id: str, owner: str) -> bool:
        row = conn.execute(
            "SELECT 1 FROM jobs WHERE job_id = ? AND lease_owner = ? AND state = ?",
            (job_id, owner, RUNNING)).fetchone()
        return row is not None
