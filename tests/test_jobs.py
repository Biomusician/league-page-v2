"""The job control plane: what survives a process that does not.

These tests are about the promises the Desk makes when something goes
wrong, so most of them are about failure. The success path is covered
where the jobs are used (test_sync_jobs, test_desk_editor); here the
question is narrower and harder: after a crash, a restart, or two workers
disagreeing, can the Commissioner still find out what was asked for, what
already happened, and whether it is safe to do it again.
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
import threading
import uuid

import pytest

from leaguepage import jobs as J
from leaguepage.job_runner import (LeaseLost, SkipStage, SoftStageError,
                                   StageError, run_job)


@pytest.fixture
def repo(tmp_path):
    return J.SQLiteJobRepository(tmp_path / "jobs.sqlite3")


def _spec(**kw):
    base = dict(job_type="publish", scope=J.SCOPE_ISSUE, league_slug="surfeit",
                season="2026", issue_key="week-01", mode="deploy",
                stages=[("snapshot", "Freeze"), ("deploy", "Ship")])
    base.update(kw)
    return J.JobSpec(**base)


def _future(**kw) -> str:
    return J.iso(J.utcnow() + dt.timedelta(**kw))


# ------------------------------------------------------------ the record

def test_a_new_job_is_queued_with_its_whole_plan_written_down(repo):
    """The plan exists before any of it runs, so a browser can show the
    pipeline and a recovery knows what was supposed to happen."""
    job, created = repo.create(_spec())
    assert created and job.state == J.QUEUED and job.active
    assert job.target_revision is None and job.attempts == 0
    stages = J.fold_stages(repo.events(job.job_id))
    assert [s["key"] for s in stages] == ["snapshot", "deploy"]
    assert all(s["status"] == J.PENDING for s in stages)
    assert job.target == "surfeit:2026:week-01"


def test_stage_state_is_a_fold_over_an_append_only_log(repo):
    job, _ = repo.create(_spec())
    repo.claim(job.job_id, "w1", 60)
    repo.append_event(job.job_id, "w1", "snapshot", J.STAGE_RUNNING, "")
    repo.append_event(job.job_id, "w1", "snapshot", J.OK, "froze r2")
    stages = {s["key"]: s for s in J.fold_stages(repo.events(job.job_id))}
    assert stages["snapshot"]["status"] == J.OK
    assert stages["snapshot"]["detail"] == "froze r2"
    assert stages["deploy"]["status"] == J.PENDING
    # The history the fold discards is still there, which is the point.
    assert [e.status for e in repo.events(job.job_id) if e.stage_key == "snapshot"] \
        == [J.PENDING, J.STAGE_RUNNING, J.OK]
    assert [s["key"] for s in J.fold_stages(repo.events(job.job_id))] \
        == ["snapshot", "deploy"]          # declaration order, not event order


# ------------------------------------------------------------ the lease

def test_only_one_of_two_racing_workers_claims_a_job(repo):
    """The guard and the write are one statement, so this cannot go both
    ways however the threads interleave."""
    job, _ = repo.create(_spec())
    gate = threading.Barrier(8)
    won: list[str] = []
    lock = threading.Lock()

    def contend(name):
        gate.wait(5)
        if repo.claim(job.job_id, name, 60):
            with lock:
                won.append(name)

    threads = [threading.Thread(target=contend, args=(f"w{i}",)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert len(won) == 1
    assert repo.get(job.job_id).lease_owner == won[0]
    assert repo.get(job.job_id).attempts == 1


def test_a_superseded_worker_cannot_write_anything(repo):
    """The whole point of the lease. A process that hung, was declared
    dead, and then woke up must not overwrite its replacement's record."""
    job, _ = repo.create(_spec())
    assert repo.claim(job.job_id, "old", -5)          # already expired
    assert repo.claim(job.job_id, "new", 60)          # taken over

    assert repo.heartbeat(job.job_id, "old", 60) is False
    assert repo.append_event(job.job_id, "old", "snapshot", J.OK, "mine") is False
    assert repo.set_result(job.job_id, "old", {"deployment_id": "dpl_old"}) is False
    assert repo.set_target_revision(job.job_id, "old", 9) is False
    assert repo.finish(job.job_id, "old", J.SUCCEEDED) is False

    # and the replacement is unaffected by any of it
    assert repo.append_event(job.job_id, "new", "snapshot", J.OK, "theirs")
    assert repo.get(job.job_id).state == J.RUNNING
    detail = J.fold_stages(repo.events(job.job_id))[0]["detail"]
    assert detail == "theirs"


def test_a_heartbeat_keeps_a_long_stage_alive(repo):
    job, _ = repo.create(_spec())
    repo.claim(job.job_id, "w1", 1)
    assert repo.heartbeat(job.job_id, "w1", 3600)
    assert not repo.reap_expired(now=_future(minutes=5))
    assert repo.get(job.job_id).state == J.RUNNING


def test_an_abandoned_job_is_lost_not_failed(repo):
    """"Failed" says the work did not happen. Nobody can say that about a
    worker that stopped reporting mid-deploy, so the record does not."""
    job, _ = repo.create(_spec())
    repo.claim(job.job_id, "w1", 60)
    lost = repo.reap_expired(now=_future(hours=1))
    assert [j.job_id for j in lost] == [job.job_id]
    after = repo.get(job.job_id)
    assert after.state == J.LOST and after.state != J.FAILED
    assert after.error_code == "lease_expired"
    assert after.lease_owner is None and not after.active
    assert repo.reap_expired(now=_future(hours=2)) == []      # only once


# ------------------------------------------------------- idempotency (§9)

def test_a_second_request_joins_the_live_job_and_a_later_one_does_not(repo):
    """Two clicks are one job. Tomorrow's click is its own job."""
    first, created1 = repo.create(_spec(idempotency_key="publish:surfeit:2026:week-01"))
    second, created2 = repo.create(_spec(idempotency_key="publish:surfeit:2026:week-01"))
    assert created1 and not created2 and first.job_id == second.job_id

    repo.claim(first.job_id, "w1", 60)
    repo.finish(first.job_id, "w1", J.SUCCEEDED)
    third, created3 = repo.create(_spec(idempotency_key="publish:surfeit:2026:week-01"))
    assert created3 and third.job_id != first.job_id


def test_a_different_issue_is_never_blocked_by_another_issues_publish(repo):
    a, _ = repo.create(_spec(idempotency_key="publish:surfeit:2026:week-01"))
    b, created = repo.create(_spec(issue_key="week-02",
                                   idempotency_key="publish:surfeit:2026:week-02"))
    assert created and a.job_id != b.job_id


def test_the_database_refuses_a_duplicate_live_key_even_if_the_code_slips(repo):
    """The uniqueness is enforced by an index, not by the application
    remembering to check."""
    job, _ = repo.create(_spec(idempotency_key="sync:all"))
    conn = sqlite3.connect(repo.db_path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO jobs (job_id, job_type, scope, state, created_at, "
                "updated_at, idempotency_key, request, result) "
                "VALUES ('x', 'sync', 'global', 'queued', ?, ?, 'sync:all', '{}', '{}')",
                (job.created_at, job.created_at))
    finally:
        conn.close()


# ------------------------------------------------ the immutable target (§9)

def test_a_publish_binds_to_one_revision_and_will_not_switch(repo):
    """The failure this tranche exists to prevent: freezing r2 and
    shipping whatever the directory says by the time npx has warmed up."""
    job, _ = repo.create(_spec())
    repo.claim(job.job_id, "w1", 60)
    assert repo.set_target_revision(job.job_id, "w1", 2)
    assert repo.set_target_revision(job.job_id, "w1", 2)     # idempotent
    assert repo.set_target_revision(job.job_id, "w1", 3) is False
    assert repo.get(job.job_id).target_revision == 2


def test_binding_refuses_through_the_stage_context_too(tmp_path, repo):
    from leaguepage.job_runner import JobContext

    job, _ = repo.create(_spec())
    repo.claim(job.job_id, "w1", 60)
    ctx = JobContext(job=repo.get(job.job_id), repo=repo, owner="w1",
                     db_path=tmp_path, log=lambda _l: None)
    ctx.bind_revision(2)
    with pytest.raises(StageError) as exc:
        ctx.bind_revision(3)
    assert exc.value.code == "target_revision_conflict"


# ------------------------------------------------------------- the runner

def _fns(calls, **overrides):
    def make(key):
        def run(ctx):
            calls.append(key)
            return f"{key} done"
        return overrides.get(key) or run
    return {k: make(k) for k in ("snapshot", "deploy")}


def test_the_runner_records_every_stage_and_ends_the_job(tmp_path, repo):
    job, _ = repo.create(_spec())
    calls: list[str] = []
    assert run_job(repo, job.job_id, _fns(calls), db_path=tmp_path) == J.SUCCEEDED
    assert calls == ["snapshot", "deploy"]
    done = repo.get(job.job_id)
    assert done.state == J.SUCCEEDED and done.ended_at and not done.active
    assert done.idempotency_key is None and done.lease_owner is None


def test_a_hard_failure_stops_the_pipeline_and_says_what_never_ran(tmp_path, repo):
    def boom(ctx):
        raise StageError("snapshot blocked: lowdown is not approved",
                         code="snapshot_blocked")

    job, _ = repo.create(_spec())
    calls: list[str] = []
    assert run_job(repo, job.job_id, _fns(calls, snapshot=boom),
                   db_path=tmp_path) == J.FAILED
    assert calls == []
    stages = {s["key"]: s for s in J.fold_stages(repo.events(job.job_id))}
    assert stages["snapshot"]["status"] == J.STAGE_FAILED
    assert stages["deploy"]["status"] == J.SKIPPED       # not "pending" forever
    assert repo.get(job.job_id).error_code == "snapshot_blocked"


def test_a_soft_failure_keeps_going_and_still_ends_failed(tmp_path, repo):
    """One league failing to sync does not throw away the other league's
    work, and the job still reports failure."""
    def soft(ctx):
        raise SoftStageError("surfeit did not sync", code="league_sync_failed")

    job, _ = repo.create(_spec())
    calls: list[str] = []
    assert run_job(repo, job.job_id, _fns(calls, snapshot=soft),
                   db_path=tmp_path) == J.FAILED
    assert calls == ["deploy"]                       # the rest still ran
    stages = {s["key"]: s for s in J.fold_stages(repo.events(job.job_id))}
    assert stages["snapshot"]["status"] == J.STAGE_FAILED
    assert stages["deploy"]["status"] == J.OK


def test_a_skipped_stage_is_not_a_failed_one(tmp_path, repo):
    def nothing_to_do(ctx):
        raise SkipStage("no league synced")

    job, _ = repo.create(_spec())
    assert run_job(repo, job.job_id, _fns([], deploy=nothing_to_do),
                   db_path=tmp_path) == J.SUCCEEDED
    stages = {s["key"]: s for s in J.fold_stages(repo.events(job.job_id))}
    assert stages["deploy"]["status"] == J.SKIPPED


def test_a_resumed_job_never_repeats_what_already_succeeded(tmp_path, repo):
    """Freezing a snapshot and deploying are not repeatable, so a worker
    that takes over an expired lease picks up where the log says the last
    one stopped."""
    job, _ = repo.create(_spec())
    repo.claim(job.job_id, "dead", -5)               # claimed, lease expired
    repo.append_event(job.job_id, "dead", "snapshot", J.OK, "froze r2")
    repo.set_target_revision(job.job_id, "dead", 2)

    calls: list[str] = []
    assert run_job(repo, job.job_id, _fns(calls), db_path=tmp_path) == J.SUCCEEDED
    assert calls == ["deploy"]                        # snapshot not run again
    assert repo.get(job.job_id).target_revision == 2  # and still bound to r2


def test_a_worker_that_cannot_claim_does_nothing_at_all(tmp_path, repo):
    job, _ = repo.create(_spec())
    repo.claim(job.job_id, "somebody", 600)
    calls: list[str] = []
    assert run_job(repo, job.job_id, _fns(calls), db_path=tmp_path) is None
    assert calls == []


def test_a_worker_stops_writing_the_moment_it_loses_the_lease(tmp_path, repo):
    """It does not fight its replacement, and it does not finish a job it
    no longer owns."""
    job, _ = repo.create(_spec())

    def declared_dead(ctx):
        # The reaper runs on every read of job state, so a stage that
        # outlives its lease can be declared dead while it is still working.
        ctx.repo.reap_expired(now=_future(hours=1))
        return "did work nobody will accept"

    calls: list[str] = []
    assert run_job(repo, job.job_id, _fns(calls, snapshot=declared_dead),
                   db_path=tmp_path) is None
    after = repo.get(job.job_id)
    # It did not finish the job it no longer owned, and it did not write
    # its stage result over the record of its own death.
    assert after.state == J.LOST and after.lease_owner is None
    assert after.error_code == "lease_expired"
    assert J.fold_stages(repo.events(job.job_id))[0]["status"] != J.OK
    assert calls == []


# ------------------------------------------------------------ recovery (§13)

def test_checkpoints_record_both_sides_of_something_irreversible(tmp_path, repo):
    from leaguepage.job_runner import JobContext

    job, _ = repo.create(_spec())
    repo.claim(job.job_id, "w1", 60)
    ctx = JobContext(job=repo.get(job.job_id), repo=repo, owner="w1",
                     db_path=tmp_path, log=lambda _l: None)
    ctx.checkpoint("deploy", before="production carried revision 1")
    ctx.checkpoint("deploy", after="dpl_abc carrying revision 2")
    marks = repo.get(job.job_id).result["checkpoints"]["deploy"]
    assert marks["before"].endswith("revision 1")
    assert marks["after"].startswith("dpl_abc")


def test_recovery_says_production_was_untouched_when_the_deploy_never_started(
        tmp_path, repo, monkeypatch):
    from leaguepage import job_recovery

    job, _ = repo.create(_spec())
    repo.claim(job.job_id, "w1", 60)
    repo.append_event(job.job_id, "w1", "snapshot", J.OK, "froze")
    repo.set_target_revision(job.job_id, "w1", 2)
    repo.reap_expired(now=_future(hours=1))

    found = job_recovery.probe(repo.db_path, job.job_id, probe_live=False)
    assert found["verdict"] == job_recovery.SNAPSHOT_ONLY
    assert found["local_only"] is True
    assert any("was not touched" in f for f in found["facts"])
    assert any("bound to revision 2" in f for f in found["facts"])


def test_recovery_reads_the_log_when_the_database_never_learned(
        tmp_path, repo, monkeypatch):
    """The crash that matters: the deploy went out and the process died
    before anything could record it. The log outlives the process."""
    from leaguepage import job_recovery
    from leaguepage import publish_jobs

    log = tmp_path / "publish.log"
    monkeypatch.setattr(publish_jobs, "_log_path", lambda _lg, _ik: log)
    write = publish_jobs._logger(log)
    write("RUN npx --yes vercel@latest deploy --prod --yes (cwd=x, timeout=600s)")
    write("EXIT 0")

    job, _ = repo.create(_spec())
    repo.claim(job.job_id, "w1", 60)
    repo.append_event(job.job_id, "w1", "snapshot", J.OK, "froze")
    repo.append_event(job.job_id, "w1", "deploy", J.STAGE_RUNNING, "")
    repo.reap_expired(now=_future(hours=1))

    found = job_recovery.probe(repo.db_path, job.job_id, probe_live=False)
    assert found["verdict"] == job_recovery.DEPLOYED
    assert any("exiting 0" in f for f in found["facts"])
    assert any("Re-deploying is safe" in n for n in found["next"])


def test_recovery_admits_it_cannot_tell(tmp_path, repo, monkeypatch):
    """A deploy that started and never reported is the one case where the
    honest answer is "go and look", and the Desk gives it."""
    from leaguepage import job_recovery
    from leaguepage import publish_jobs

    log = tmp_path / "publish.log"
    monkeypatch.setattr(publish_jobs, "_log_path", lambda _lg, _ik: log)
    publish_jobs._logger(log)("RUN npx --yes vercel@latest deploy --prod --yes")

    job, _ = repo.create(_spec())
    repo.claim(job.job_id, "w1", 60)
    repo.append_event(job.job_id, "w1", "deploy", J.STAGE_RUNNING, "")
    repo.reap_expired(now=_future(hours=1))

    found = job_recovery.probe(repo.db_path, job.job_id, probe_live=False)
    assert found["verdict"] == job_recovery.UNCERTAIN
    assert "may or may not" in found["summary"]
    assert any("Open the published issue" in n for n in found["next"])


def test_recovery_never_re_runs_anything(tmp_path, repo, monkeypatch):
    """It reports. A person decides. Re-running the stage that may already
    have changed production is the thing this must never do."""
    from leaguepage import job_recovery
    from leaguepage import publish_jobs

    called = []
    monkeypatch.setattr(publish_jobs, "_run",
                        lambda *a, **kw: called.append(a) or None)
    job, _ = repo.create(_spec())
    repo.claim(job.job_id, "w1", 60)
    repo.reap_expired(now=_future(hours=1))
    job_recovery.probe(repo.db_path, job.job_id, probe_live=False)
    assert called == []
    assert repo.get(job.job_id).state == J.LOST      # unchanged by looking


# ------------------------------------------------------------ retention (§19)

def test_purge_drops_old_history_and_keeps_what_is_still_useful(repo):
    old = J.iso(J.utcnow() - dt.timedelta(days=90))
    conn = sqlite3.connect(repo.db_path)
    try:
        for i in range(60):
            conn.execute(
                "INSERT INTO jobs (job_id, job_type, scope, state, created_at, "
                "updated_at, request, result) VALUES (?, 'publish', 'issue', "
                "'succeeded', ?, ?, '{}', '{}')", (f"old{i}", old, old))
            conn.execute(
                "INSERT INTO job_events (job_id, seq, stage_key, status, at) "
                "VALUES (?, 0, 'snapshot', 'ok', ?)", (f"old{i}", old))
        conn.commit()
    finally:
        conn.close()
    live, _ = repo.create(_spec())
    repo.claim(live.job_id, "w1", 600)

    repo.purge(keep_days=30, keep_per_type=10)
    assert repo.get(live.job_id) is not None   # a running job is never purged
    conn = sqlite3.connect(repo.db_path)
    try:
        rows = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        events = conn.execute("SELECT COUNT(*) FROM job_events").fetchone()[0]
    finally:
        conn.close()
    # Ten kept per type: the running job is the most recent of them, so it
    # takes one slot and nine of the old ones fill the rest. Events go with
    # their job, so what is left is the nine old ones plus the live job's
    # own two-stage plan.
    assert rows == 10 and events == 9 + 2


# --------------------------------------------- portability and cost (§17/§22)

def test_a_job_id_is_something_postgres_would_accept_as_a_uuid(repo):
    job, _ = repo.create(_spec())
    assert uuid.UUID(hex=job.job_id)
    assert len(job.job_id) == 32


def test_timestamps_sort_the_same_as_text_and_as_time(repo):
    """SQLite compares these as strings and Postgres as timestamps. That is
    only safe while every one of them is UTC, second-resolution and the
    same width, so the invariant is pinned rather than assumed."""
    stamps = [J.iso(J.utcnow() + dt.timedelta(seconds=s))
              for s in (0, 1, 59, 60, 3600, 86400, 864000)]
    assert stamps == sorted(stamps)
    for s in stamps:
        assert s.endswith("+00:00") and len(s) == len(stamps[0])
        assert dt.datetime.fromisoformat(s).tzinfo == dt.timezone.utc


def test_the_sqlite_repository_implements_the_whole_contract():
    """A PostgresJobRepository has to be able to replace this one, so the
    contract is checked as a contract rather than trusted."""
    import inspect

    wanted = {n for n, _ in inspect.getmembers(J.JobRepository)
              if not n.startswith("_")}
    assert wanted                              # the protocol is not empty
    for name in wanted:
        impl = getattr(J.SQLiteJobRepository, name, None)
        assert callable(impl), f"SQLiteJobRepository is missing {name}"
        proto = inspect.signature(getattr(J.JobRepository, name))
        assert inspect.signature(impl).parameters.keys() == proto.parameters.keys(), name


def test_every_query_that_runs_on_a_timer_has_an_index(repo):
    conn = sqlite3.connect(repo.db_path)
    try:
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index'")}
        assert {"idx_jobs_idempotency", "idx_jobs_recent", "idx_jobs_target",
                "idx_jobs_lease", "idx_job_events_seq"} <= names
        # The reaper is the one query on a poll; it must not scan.
        plan = " ".join(str(r) for r in conn.execute(
            "EXPLAIN QUERY PLAN SELECT * FROM jobs WHERE state = 'running' "
            "AND lease_expires_at < '2026-01-01'"))
        assert "idx_jobs_lease" in plan and "SCAN" not in plan
    finally:
        conn.close()


# -------------------------------------------------------- migration (§16)

def test_the_tables_arrive_on_a_populated_database_without_disturbing_it(tmp_path):
    """The real database has a season of editorial state in it. Adding a
    control plane must be an addition and nothing else."""
    from leaguepage.config import get_league
    from leaguepage.storage import Storage

    from fixtures import populate_league

    db = tmp_path / "populated.sqlite3"
    with Storage(db) as s:
        populate_league(s, get_league("surfeit"), teams=10, rounds=3)
        s.set_meta("current_week", "3")
        s.set_meta("deploy_state:surfeit:2026:week-01",
                   json.dumps({"state": "deployed", "revision": 2}))
        before = {r[0] for r in s._conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}

    # Reopening applies the schema again, as every Desk start does.
    with Storage(db) as s:
        after = {r[0] for r in s._conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert after - before == set()          # already there the first time
        assert {"jobs", "job_events"} <= after
        assert s.get_meta("current_week") == "3"
        assert json.loads(s.get_meta("deploy_state:surfeit:2026:week-01"))["revision"] == 2
        assert s.get_league_users(get_league("surfeit").league_id)

    repo = J.SQLiteJobRepository(db)
    job, created = repo.create(_spec())
    assert created and repo.get(job.job_id).job_id == job.job_id
