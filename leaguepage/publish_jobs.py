"""Publish/deploy jobs for the Commissioner's Desk.

The old publish endpoints held one HTTP POST open through snapshot ->
build -> npx vercel link -> npx vercel deploy -> remote verification
(30-90s, longer on a cold npx cache, potentially forever if npx prompted
on stdin). The browser showed a dead spinner while the pipeline quietly
succeeded, which invited double deploys. So the POST creates a job and
returns, a worker runs the stages, and the browser polls.

That fixed the spinner and left a worse problem underneath: the job lived
in a dict in this process. `uvicorn --reload` watches `leaguepage/`, so
editing any file here restarted the Desk and took every running job's
state with it, while the `vercel deploy` it had spawned carried on and
changed production. What the Commissioner was left with was a live site
that may or may not carry his correction and a Desk with no memory of
trying.

Now the job is a row (see `jobs`), and three things follow from that:

* **The publish is bound to one immutable revision.** The snapshot stage
  pins `target_revision`, and every later stage ships that number rather
  than re-reading the directory. A job that froze r2 cannot deploy r3
  because somebody saved an edit while npx was warming up.
* **Production state is recorded when production changes**, not when the
  job ends. The deploy stage writes `deployed-unverified` the moment the
  deployment goes out, and verification upgrades it. The window where a
  crash could lose the fact of a deploy is now one statement wide instead
  of two stages wide.
* **Both sides of anything irreversible are checkpointed**, so
  `job_recovery` can ask "did this actually happen" of a job nobody was
  watching when it died.

Every child process still runs with stdin closed and an explicit timeout,
and a timeout kills the whole process tree. Stage-by-stage progress,
stdout/stderr tails and results go to logs/publish-{league}-{issue}.log
(gitignored; no credentials are ever read, so none can leak). That log is
also the crash-proof evidence a recovery reads.

`published` still means "immutable snapshot frozen locally"; production
state lives in the meta key deploy_state:{league}:{season}:{issue}.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

from leaguepage import jobs as jobs_mod
from leaguepage.config import DIST_DIR, REPO_ROOT, get_league
from leaguepage.job_runner import JobContext, LeaseLost, StageError
from leaguepage.storage import Storage

PRODUCTION_URL = "https://league-page-ten-sandy.vercel.app"
VERCEL_PROJECT = "league-page"

JOB_TYPE = "publish"

TIMEOUTS = {"build": 300, "link": 240, "deploy": 600, "verify": 25}
# A production alias can take a few seconds to answer after "Ready";
# one probe a second after the deploy is not a verdict on the deployment.
VERIFY_ATTEMPTS = 6
VERIFY_PAUSE = 5

# Long enough that a stage's own timeout is the thing that stops a stage,
# short enough that a dead process is noticed in a minute and a half. The
# worker heartbeats every 30s while a stage runs, so the six-minute deploy
# does not need a six-minute lease.
LEASE_SECONDS = 90

STAGES_LOCAL = [("snapshot", "Creating immutable issue snapshot"),
                ("build", "Building public site + privacy audit")]
STAGES_DEPLOY = STAGES_LOCAL + [("deploy", "Deploying to Vercel production"),
                                ("verify", "Verifying production URLs")]
CORRECTION_STAGE_NAME = "Freezing a correction beside the original snapshot"

LIVE_STATES = ("deployed", "deployed-unverified")


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _issue_key(league_slug: str, season: str, issue_key: str) -> str:
    return f"{league_slug}:{season}:{issue_key}"


def _repo(db_path) -> jobs_mod.SQLiteJobRepository:
    return jobs_mod.SQLiteJobRepository(db_path)


def _log_path(league_slug: str, issue_key: str) -> Path:
    p = REPO_ROOT / "logs" / f"publish-{league_slug}-{issue_key}.log"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _logger(path: Path):
    def log(line: str) -> None:
        # defense in depth: no credential value may enter the log even if a
        # CLI ever echoes one (JWT-shaped strings and KEY=value secrets)
        line = re.sub(r"eyJ[A-Za-z0-9_\-]{20,}", "[redacted-jwt]", line)
        line = re.sub(r"((?:TOKEN|SECRET|KEY|PASSWORD)[A-Z_]*\s*[=:]\s*)\S{8,}",
                      r"\1[redacted]", line)
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{_now()} {line}\n")
    return log


def _run(ctx: JobContext, cmd: list[str], *, cwd: Path, timeout: int,
         env: dict | None = None) -> subprocess.CompletedProcess:
    """Run a child process: stdin closed, explicit timeout, tree-kill on
    timeout so an interactive prompt can never hang a publish forever."""
    ctx.log(f"RUN {' '.join(cmd)} (cwd={cwd}, timeout={timeout}s)")
    proc = subprocess.Popen(cmd, cwd=cwd, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env=env, text=True, encoding="utf-8",
                            errors="replace")
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True)
        else:
            proc.kill()
        proc.wait(timeout=10)
        ctx.log(f"TIMEOUT after {timeout}s; process tree killed")
        raise StageError(f"timed out after {timeout}s (process terminated)",
                         code="stage_timeout")
    ctx.log(f"EXIT {proc.returncode}")
    if out:
        ctx.log("STDOUT: " + out.strip()[-4000:])
    if err:
        ctx.log("STDERR: " + err.strip()[-4000:])
    return subprocess.CompletedProcess(cmd, proc.returncode, out or "", err or "")


# ------------------------------------------------------------- the job API

def as_dict(repo, job: jobs_mod.Job) -> dict:
    """The shape the publish screen and the Issue Room drawer render.

    The same keys the in-memory job exposed, so durability cost the UI
    nothing, plus `active` (a job can exist before a worker claims it) and
    `revision` promoted out of an ad-hoc attribute into the column that
    binds the job to what it is shipping.
    """
    result = job.result or {}
    key = _issue_key(job.league_slug, job.season, job.issue_key)
    return {
        "job_id": job.job_id, "issue": key,
        "league_slug": job.league_slug, "season": job.season,
        "issue_key": job.issue_key, "mode": job.mode,
        "note": (job.request or {}).get("note"),
        "state": job.state, "active": job.active,
        "created_at": job.created_at, "ended_at": job.ended_at,
        "error": job.error, "error_code": job.error_code,
        "stages": jobs_mod.fold_stages(repo.events(job.job_id)),
        "production_url": PRODUCTION_URL,
        "issue_url": f"{PRODUCTION_URL}/{job.league_slug}/{job.season}/{job.issue_key}/",
        "deployment_id": result.get("deployment_id"),
        "revision": job.target_revision,
        "checkpoints": result.get("checkpoints", {}),
        "log_path": str(_log_path(job.league_slug, job.issue_key)),
    }


def get_job(db_path, job_id: str) -> dict | None:
    repo = _repo(db_path)
    job = repo.get(job_id)
    return as_dict(repo, job) if job else None


def get_job_for(db_path, league_slug: str, season: str,
                issue_key: str) -> dict | None:
    """Most recent job for this issue (running or finished), for refresh
    recovery and the progress panel. It survives a Desk restart now, which
    is the whole point: the panel can report on a job this process never
    ran."""
    repo = _repo(db_path)
    # Same as the sync side: the read path is where a lease nobody is
    # renewing gets expired, so a publish whose Desk restarted mid-deploy
    # turns up as lost rather than as a spinner that never resolves.
    repo.reap_expired()
    job = repo.latest(JOB_TYPE, league_slug=league_slug, season=season,
                      issue_key=issue_key)
    return as_dict(repo, job) if job else None


def start_publish_job(db_path, league_slug: str, season: str, issue_key: str,
                      mode: str, *, note: str | None = None) -> tuple[dict, bool]:
    """(job, created). A live job for the same issue is returned as-is:
    duplicate clicks can never start duplicate production deployments, and
    that now holds across a restart rather than only within one process.

    `note` turns the snapshot stage into a correction: the original stays
    on disk and a sibling revision is frozen beside it."""
    from leaguepage.job_runner import start_in_thread

    note = (note or "").strip() or None
    stages = [(k, CORRECTION_STAGE_NAME if (k == "snapshot" and note) else n)
              for k, n in (STAGES_DEPLOY if mode == "deploy" else STAGES_LOCAL)]
    repo = _repo(db_path)
    job, created = repo.create(jobs_mod.JobSpec(
        job_type=JOB_TYPE, scope=jobs_mod.SCOPE_ISSUE,
        league_slug=league_slug, season=season, issue_key=issue_key,
        mode=mode, request={"note": note},
        # One live publish per issue. Released when the job ends, so the
        # next correction is not blocked by the one that shipped it.
        idempotency_key=f"publish:{_issue_key(league_slug, season, issue_key)}",
        stages=stages))
    if created:
        log = _logger(_log_path(league_slug, issue_key))
        log(f"---- publish job {job.job_id} mode={mode}"
            + (" correction" if note else "") + " ----")
        start_in_thread(repo, job.job_id, _STAGE_FNS, db_path=db_path,
                        lease_seconds=LEASE_SECONDS, log=log,
                        on_finish=_on_finish)
    return as_dict(repo, job), created


# ------------------------------------------------------- production record

def _on_finish(ctx: JobContext, state: str) -> None:
    """What production actually carries, kept separate from how the job
    ended. A job that died at the snapshot or build stage never touched
    production, so the previous record stands and the attempt is noted
    beside it; a deploy that went out but failed verification is still a
    deploy, and is recorded as one."""
    if ctx.job.mode != "deploy":
        return
    stages = {s["key"]: s for s in
              jobs_mod.fold_stages(ctx.repo.events(ctx.job.job_id))}
    deploy_st = stages.get("deploy", {"status": "pending", "detail": ""})
    verify_st = stages.get("verify", {"status": "pending", "detail": ""})
    now = _now()
    key = f"deploy_state:{_issue_key(ctx.job.league_slug, ctx.job.season, ctx.job.issue_key)}"
    with Storage(ctx.db_path) as s:
        if deploy_st["status"] == jobs_mod.OK:
            verified = verify_st["status"] == jobs_mod.OK
            record = {
                "state": "deployed" if verified else "deployed-unverified",
                "at": now,
                "url": f"{PRODUCTION_URL}/{ctx.job.league_slug}/"
                       f"{ctx.job.season}/{ctx.job.issue_key}/",
                "deployment_id": ctx.result.get("deployment_id"),
                "verified": verified,
                "revision": ctx.job.target_revision,
                "reason": None if verified else verify_st["detail"][:300]}
        elif deploy_st["status"] == jobs_mod.STAGE_FAILED:
            record = {"state": "deploy-failed", "at": now, "url": None,
                      "deployment_id": None,
                      "reason": deploy_st["detail"][:300]}
        else:
            prior = s.get_meta(key)
            record = json.loads(prior) if prior else {
                "state": "never-deployed", "at": None, "url": None,
                "deployment_id": None}
            failed = next((st for st in stages.values()
                           if st["status"] == jobs_mod.STAGE_FAILED), None)
            record["last_attempt"] = {
                "at": now, "failed_stage": failed["key"] if failed else None,
                "reason": (failed["detail"] if failed else "")[:300]}
        s.set_meta(key, json.dumps(record))
        if deploy_st["status"] == jobs_mod.OK:
            _mark_shipped_revisions(s, ctx, record, now)


def _mark_shipped_revisions(s: Storage, ctx: JobContext, record: dict,
                            now: str) -> None:
    """A deploy ships the whole built site, not one issue. Every published
    issue whose latest frozen revision was not yet on production went out
    with this deployment, so its record says so; an issue already live at
    its latest revision keeps its own timestamp, because its readers saw
    nothing new."""
    from leaguepage import config as cfg
    from leaguepage.publish import REVISION_RE

    root = Path(cfg.PUBLISHED_DIR)
    if not root.exists():
        return
    mine = _issue_key(ctx.job.league_slug, ctx.job.season, ctx.job.issue_key)
    latest: dict[tuple[str, str, str], int] = {}
    for path in root.glob("*/*/*.json"):
        league_slug, season = path.parent.parent.name, path.parent.name
        m = REVISION_RE.match(path.stem)
        key, n = (m.group("key"), int(m.group("n"))) if m else (path.stem, 1)
        latest[(league_slug, season, key)] = max(
            latest.get((league_slug, season, key), 0), n)
    for (league_slug, season, key), n in latest.items():
        meta_key = f"deploy_state:{_issue_key(league_slug, season, key)}"
        if meta_key == f"deploy_state:{mine}":
            continue
        raw = s.get_meta(meta_key)
        prior = json.loads(raw) if raw else None
        if prior and prior.get("state") in LIVE_STATES:
            if prior.get("revision") is None:
                prior["revision"] = n            # older record: fill, keep its time
                s.set_meta(meta_key, json.dumps(prior))
                continue
            if prior["revision"] >= n:
                continue
        s.set_meta(meta_key, json.dumps({
            "state": record["state"], "at": now,
            "url": f"{PRODUCTION_URL}/{league_slug}/{season}/{key}/",
            "deployment_id": ctx.result.get("deployment_id"),
            "verified": record.get("verified"),
            "revision": n, "via": mine, "reason": None}))


def deploy_state(storage: Storage, league_slug: str, season: str,
                 issue_key: str) -> dict | None:
    raw = storage.get_meta(
        f"deploy_state:{_issue_key(league_slug, season, issue_key)}")
    return json.loads(raw) if raw else None


def last_public_change(storage: Storage, league_slug: str,
                       now: dt.datetime | None = None) -> dict | None:
    """The most recent deploy that reached production for this league, any
    issue: {"issue_key", "season", "revision", "at", "ago"}. None when the
    Desk has never deployed it. Production changes only through a deploy,
    so this is when a reader last saw something new."""
    best = None
    for key, raw in storage.list_meta(f"deploy_state:{league_slug}:").items():
        try:
            rec = json.loads(raw)
        except ValueError:
            continue
        if rec.get("state") not in LIVE_STATES or not rec.get("at"):
            continue
        if best is None or rec["at"] > best["at"]:
            _prefix, _slug, season, issue_key = key.split(":", 3)
            best = {"issue_key": issue_key, "season": season, "at": rec["at"],
                    "revision": rec.get("revision"), "state": rec["state"]}
    if best:
        best["ago"] = ago(best["at"], now=now)
    return best


def ago(iso: str, now: dt.datetime | None = None) -> str:
    """"10 minutes ago", "5 days ago": how a person says when."""
    try:
        then = dt.datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return "at an unknown time"
    if then.tzinfo is None:
        then = then.replace(tzinfo=dt.timezone.utc)
    now = now or dt.datetime.now(dt.timezone.utc)
    secs = max(0, int((now - then).total_seconds()))
    if secs < 60:
        return "just now"
    for unit, size in (("minute", 60), ("hour", 3600), ("day", 86400)):
        n = secs // size
        if secs < size * (60 if unit == "minute" else 24 if unit == "hour" else 10**9):
            return f"{n} {unit}{'' if n == 1 else 's'} ago"
    return "long ago"


def revision_number(path: Path) -> int:
    """week-01.json is 1; week-01.r3.json is 3."""
    from leaguepage.publish import REVISION_RE

    m = REVISION_RE.match(path.stem)
    return int(m.group("n")) if m else 1


# ------------------------------------------------------------------ stages

def _stage_snapshot(ctx: JobContext) -> str:
    """Freeze the issue, and bind this job to what it froze.

    Writing a snapshot file is not undoable, so both sides of it are
    checkpointed: what the family looked like before, and what exists
    after. `bind_revision` is the other guard — from here on the job ships
    a number, not whatever the directory happens to say later.
    """
    from leaguepage import config as cfg
    from leaguepage.publish import (publish_assembled_issue, revise_issue,
                                    snapshot_family, text_changed_since_publish)

    job = ctx.job
    league = get_league(job.league_slug)
    week = (int(job.issue_key.removeprefix("week-"))
            if job.issue_key.startswith("week-") else None)
    note = (job.request or {}).get("note")
    family = snapshot_family(cfg.PUBLISHED_DIR, league.slug, job.season,
                             job.issue_key)
    ctx.checkpoint("snapshot", before=", ".join(p.name for p in family) or "none")
    try:
        with Storage(ctx.db_path) as s:
            changed = (text_changed_since_publish(
                s, league, job.season, job.issue_key, week=week)
                if family else None)
            if changed is False:
                # Nothing to freeze. A note left in the box is not a reason
                # to manufacture a revision; the deploy still runs, because
                # the site around the issue may have changed.
                ctx.bind_revision(revision_number(family[-1]))
                ctx.checkpoint("snapshot", after=f"unchanged at {family[-1].name}")
                return (f"unchanged since {family[-1].name}; nothing new frozen"
                        + (" (the note was not needed)" if note else ""))
            if changed and not note:
                raise StageError(
                    "the text has changed since this issue was published; publishing "
                    "again would rewrite the record of what shipped. Add a correction "
                    "note on the publish page and the change ships as a revision.",
                    code="text_changed_needs_note")
            if note:
                snap = revise_issue(s, league, job.season, job.issue_key,
                                    note=note, week=week)
                ctx.bind_revision(revision_number(snap))
                ctx.checkpoint("snapshot", after=snap.name)
                return (f"correction r{ctx.job.target_revision} frozen beside "
                        f"the original: {snap}")
            snap = publish_assembled_issue(s, league, job.season, job.issue_key,
                                           week=week)
            ctx.bind_revision(1)
            ctx.checkpoint("snapshot", after=snap.name)
    except (StageError, LeaseLost):
        # A lost lease is not a snapshot failure. Recording it as one would
        # write this worker's verdict over the record of its replacement.
        raise
    except Exception as exc:
        raise StageError(f"snapshot blocked: {exc}", code="snapshot_blocked") from exc
    return f"frozen: {snap}"


def _stage_build(ctx: JobContext) -> str:
    import os

    py = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
    # The child inherits a cp1252 console; its output is decoded as UTF-8
    # here, so tell it to write UTF-8 or every em-dash logs as a "?".
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    proc = _run(ctx, [str(py if py.exists() else sys.executable),
                      str(REPO_ROOT / "scripts" / "build_public_site.py")],
                cwd=REPO_ROOT, timeout=TIMEOUTS["build"], env=env)
    if proc.returncode != 0:
        tail = (proc.stdout + proc.stderr).strip()[-300:]
        raise StageError(f"build/privacy audit failed: {tail}",
                         code="build_failed")
    return (proc.stdout or "").strip().splitlines()[-1][:200]


def _vercel_env(ctx: JobContext) -> dict:
    """Vercel CLI resolves its credential dir from the environment; a Desk
    process whose env differs from the shell that ran `vercel login` can see
    an empty config dir and report "No existing credentials found". Pin
    XDG_DATA_HOME to whichever candidate dir actually holds auth.json.

    Caveat learned 2026-08-30: processes inside a Claude Code session see a
    private overlay copy of auth.json that does not exist on the real disk.
    A HIT here from such a process proves nothing about the machine; the
    authoritative check is `vercel whoami` from a normal user terminal."""
    import os

    env = dict(os.environ)
    home = Path(os.environ.get("USERPROFILE") or Path.home())
    candidates = []
    if env.get("XDG_DATA_HOME"):
        candidates.append(Path(env["XDG_DATA_HOME"]))
    if env.get("APPDATA"):
        candidates.append(Path(env["APPDATA"]) / "xdg.data")
    candidates += [home / "AppData" / "Roaming" / "xdg.data",
                   home / ".local" / "share"]
    for c in candidates:
        if (c / "com.vercel.cli" / "auth.json").exists():
            env["XDG_DATA_HOME"] = str(c)
            break
    ctx.log(f"deploy env: APPDATA={env.get('APPDATA')!r} "
            f"USERPROFILE={env.get('USERPROFILE')!r} "
            f"HOME={env.get('HOME')!r}")
    ctx.log("auth candidates: " + "; ".join(
        f"{c} -> {'HIT' if (c / 'com.vercel.cli' / 'auth.json').exists() else 'miss'}"
        for c in candidates))
    ctx.log(f"pinned XDG_DATA_HOME: {env.get('XDG_DATA_HOME', '(none)')}")
    return env


def _stage_deploy(ctx: JobContext) -> str:
    """The only stage that changes what a reader sees.

    Both sides are checkpointed and the production record is written the
    moment the deployment goes out, rather than when the job ends. If this
    process dies during verification, the Desk still knows a deploy
    happened and which revision it carried.
    """
    npx = shutil.which("npx") or shutil.which("npx.cmd")
    if not npx:
        raise StageError("npx not found on PATH; deploy from a terminal instead",
                         code="npx_missing")
    env = _vercel_env(ctx)
    with Storage(ctx.db_path) as s:
        prior = deploy_state(s, ctx.job.league_slug, ctx.job.season,
                             ctx.job.issue_key) or {}
    ctx.checkpoint(
        "deploy",
        before=f"production carried revision {prior.get('revision')} "
               f"({prior.get('deployment_id') or 'no deployment id'}) "
               f"as of {prior.get('at') or 'never'}")

    who = _run(ctx, [npx, "--yes", "vercel@latest", "whoami"], cwd=DIST_DIR,
               timeout=TIMEOUTS["link"], env=env)
    if who.returncode != 0:
        raise StageError(
            "Vercel is logged out on this computer. One-time fix: open a "
            "regular PowerShell window (not through Claude), run "
            "`npx vercel login`, finish the login in the browser, then come "
            "back here and retry. Note for future debugging: a terminal run "
            "by a Claude Code session is NOT a valid test of this — those "
            "sessions see a private copy of the credential file that the "
            "rest of the machine cannot (verified 2026-08-30), so `vercel "
            "login` must be run in Jonathan's own terminal to count.",
            code="vercel_logged_out")
    link = _run(ctx, [npx, "--yes", "vercel@latest", "link", "--yes",
                      "--project", VERCEL_PROJECT], cwd=DIST_DIR,
                timeout=TIMEOUTS["link"], env=env)
    if link.returncode != 0:
        raise StageError("vercel link failed: "
                         + (link.stderr or link.stdout).strip()[-250:],
                         code="vercel_link_failed")
    dep = _run(ctx, [npx, "--yes", "vercel@latest", "deploy", "--prod", "--yes"],
               cwd=DIST_DIR, timeout=TIMEOUTS["deploy"], env=env)
    out = (dep.stdout or "") + (dep.stderr or "")
    if dep.returncode != 0:
        raise StageError("vercel deploy failed: " + out.strip()[-250:],
                         code="vercel_deploy_failed")
    m = re.search(r"/league-page/([A-Za-z0-9]+)", out)
    deployment_id = f"dpl_{m.group(1)}" if m else None
    ctx.result["deployment_id"] = deployment_id
    ctx.checkpoint("deploy", after=f"{deployment_id or '(id not reported)'} "
                                   f"carrying revision {ctx.job.target_revision}")
    _record_deployed(ctx, deployment_id)
    return f"deployment {deployment_id or '(id not reported)'}"


def _record_deployed(ctx: JobContext, deployment_id: str | None) -> None:
    """Write the production record now, as unverified.

    Production has already changed by the time this runs. Waiting for the
    job to end before saying so is what let a crash during verification
    leave the Desk claiming an issue was never deployed while readers were
    looking at it.
    """
    key = f"deploy_state:{_issue_key(ctx.job.league_slug, ctx.job.season, ctx.job.issue_key)}"
    with Storage(ctx.db_path) as s:
        s.set_meta(key, json.dumps({
            "state": "deployed-unverified", "at": _now(),
            "url": f"{PRODUCTION_URL}/{ctx.job.league_slug}/"
                   f"{ctx.job.season}/{ctx.job.issue_key}/",
            "deployment_id": deployment_id, "verified": False,
            "revision": ctx.job.target_revision,
            "reason": "deployed; verification had not run yet"}))


def _probe_url(url: str, timeout: int | None = None) -> int:
    """HTTP status of one GET; an HTTP error is its code, anything else 0.

    Verification can afford to wait; a recovery finding rendered on a
    status poll cannot, so it passes a shorter timeout.
    """
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(
                url, timeout=timeout or TIMEOUTS["verify"]) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except Exception:
        return 0


def _stage_verify(ctx: JobContext) -> str:
    import time

    paths = ("/", f"/{ctx.job.league_slug}/",
             f"/{ctx.job.league_slug}/{ctx.job.season}/{ctx.job.issue_key}/")
    detail = ""
    for attempt in range(1, VERIFY_ATTEMPTS + 1):
        checks = [(p, _probe_url(PRODUCTION_URL + p)) for p in paths]
        detail = ", ".join(f"{p} -> {c or 'unreachable'}" for p, c in checks)
        if all(c == 200 for _, c in checks):
            return detail + (f" (attempt {attempt})" if attempt > 1 else "")
        ctx.log(f"verify attempt {attempt}/{VERIFY_ATTEMPTS}: {detail}")
        if attempt < VERIFY_ATTEMPTS:
            time.sleep(VERIFY_PAUSE)
    raise StageError(
        f"production verification failed after {VERIFY_ATTEMPTS} attempts: "
        f"{detail}. The deployment itself went out; check the URLs by hand.",
        code="verify_failed")


_STAGE_FNS = {"snapshot": _stage_snapshot, "build": _stage_build,
              "deploy": _stage_deploy, "verify": _stage_verify}
