"""In-process publish/deploy jobs for the Commissioner's Desk.

The old publish endpoints held one HTTP POST open through snapshot ->
build -> npx vercel link -> npx vercel deploy -> remote verification
(30-90s, longer on a cold npx cache, potentially forever if npx prompted
on stdin). The browser showed a dead spinner while the pipeline quietly
succeeded, which invited double deploys.

Now: POST /publish-start creates a job and returns immediately; a daemon
thread runs the stages; the browser polls /publish-status. One job per
issue at a time; duplicate starts return the existing job. Every child
process runs with stdin closed and an explicit timeout, and a timeout
kills the whole process tree. Stage-by-stage progress, stdout/stderr
tails, and results go to logs/publish-{league}-{issue}.log (gitignored;
no credentials are ever read, so none can leak). The deploy outcome is
recorded separately from the snapshot lifecycle: `published` keeps
meaning "immutable snapshot frozen locally"; production state lives in
the meta key deploy_state:{league}:{season}:{issue}.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import shutil
import subprocess
import sys
import threading
import uuid
from pathlib import Path

from leaguepage.config import DIST_DIR, REPO_ROOT, get_league
from leaguepage.storage import Storage

PRODUCTION_URL = "https://league-page-ten-sandy.vercel.app"
VERCEL_PROJECT = "league-page"

TIMEOUTS = {"build": 300, "link": 240, "deploy": 600, "verify": 25}
# A production alias can take a few seconds to answer after "Ready";
# one probe a second after the deploy is not a verdict on the deployment.
VERIFY_ATTEMPTS = 6
VERIFY_PAUSE = 5

_LOCK = threading.Lock()
_JOBS: dict[str, dict] = {}          # job_id -> job
_ACTIVE: dict[str, str] = {}         # issue key -> job_id of running job

STAGES_LOCAL = [("snapshot", "Creating immutable issue snapshot"),
                ("build", "Building public site + privacy audit")]
STAGES_DEPLOY = STAGES_LOCAL + [("deploy", "Deploying to Vercel production"),
                                ("verify", "Verifying production URLs")]
CORRECTION_STAGE_NAME = "Freezing a correction beside the original snapshot"


class StageError(Exception):
    pass


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _issue_key(league_slug: str, season: str, issue_key: str) -> str:
    return f"{league_slug}:{season}:{issue_key}"


def _log_path(league_slug: str, issue_key: str) -> Path:
    p = REPO_ROOT / "logs" / f"publish-{league_slug}-{issue_key}.log"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _log(job: dict, line: str) -> None:
    # defense in depth: no credential value may enter the log even if a CLI
    # ever echoes one (JWT-shaped strings and KEY=value secrets)
    line = re.sub(r"eyJ[A-Za-z0-9_\-]{20,}", "[redacted-jwt]", line)
    line = re.sub(r"((?:TOKEN|SECRET|KEY|PASSWORD)[A-Z_]*\s*[=:]\s*)\S{8,}",
                  r"\1[redacted]", line)
    with open(job["log_path"], "a", encoding="utf-8") as f:
        f.write(f"{_now()} {line}\n")


def _run(job: dict, cmd: list[str], *, cwd: Path, timeout: int,
         env: dict | None = None) -> subprocess.CompletedProcess:
    """Run a child process: stdin closed, explicit timeout, tree-kill on
    timeout so an interactive prompt can never hang a publish forever."""
    _log(job, f"RUN {' '.join(cmd)} (cwd={cwd}, timeout={timeout}s)")
    proc = subprocess.Popen(cmd, cwd=cwd, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env=env,
                            text=True, encoding="utf-8", errors="replace")
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True)
        else:
            proc.kill()
        proc.wait(timeout=10)
        _log(job, f"TIMEOUT after {timeout}s; process tree killed")
        raise StageError(f"timed out after {timeout}s (process terminated)")
    _log(job, f"EXIT {proc.returncode}")
    if out:
        _log(job, "STDOUT: " + out.strip()[-4000:])
    if err:
        _log(job, "STDERR: " + err.strip()[-4000:])
    return subprocess.CompletedProcess(cmd, proc.returncode, out or "", err or "")


def get_job(job_id: str) -> dict | None:
    with _LOCK:
        return _JOBS.get(job_id)


def get_job_for(league_slug: str, season: str, issue_key: str) -> dict | None:
    """Most recent job for this issue (running or finished), for refresh
    recovery and the progress panel."""
    with _LOCK:
        candidates = [j for j in _JOBS.values()
                      if j["issue"] == _issue_key(league_slug, season, issue_key)]
    return max(candidates, key=lambda j: j["created_at"]) if candidates else None


def start_publish_job(db_path, league_slug: str, season: str, issue_key: str,
                      mode: str, *, note: str | None = None) -> tuple[dict, bool]:
    """(job, created). A running job for the same issue is returned as-is:
    duplicate clicks can never start duplicate production deployments.

    `note` turns the snapshot stage into a correction: the original stays
    on disk and a sibling revision is frozen beside it."""
    key = _issue_key(league_slug, season, issue_key)
    note = (note or "").strip() or None
    with _LOCK:
        active_id = _ACTIVE.get(key)
        if active_id and _JOBS[active_id]["state"] == "running":
            return _JOBS[active_id], False
        stages = [(k, CORRECTION_STAGE_NAME if (k == "snapshot" and note) else n)
                  for k, n in (STAGES_DEPLOY if mode == "deploy" else STAGES_LOCAL)]
        job = {
            "job_id": uuid.uuid4().hex[:12],
            "issue": key,
            "league_slug": league_slug, "season": season, "issue_key": issue_key,
            "mode": mode, "note": note, "state": "running",
            "created_at": _now(), "ended_at": None,
            "stages": [{"key": k, "name": n, "status": "pending", "detail": ""}
                       for k, n in stages],
            "production_url": PRODUCTION_URL,
            "issue_url": f"{PRODUCTION_URL}/{league_slug}/{season}/{issue_key}/",
            "deployment_id": None,
            "log_path": str(_log_path(league_slug, issue_key)),
        }
        _JOBS[job["job_id"]] = job
        _ACTIVE[key] = job["job_id"]
    _log(job, f"---- publish job {job['job_id']} mode={mode}"
              + (" correction" if note else "") + " ----")
    threading.Thread(target=_run_job, args=(job, db_path), daemon=True).start()
    return job, True


def _stage(job: dict, key: str) -> dict:
    return next(s for s in job["stages"] if s["key"] == key)


def _run_job(job: dict, db_path) -> None:
    try:
        for stage in job["stages"]:
            stage["status"] = "running"
            _log(job, f"STAGE {stage['key']} start")
            detail = _STAGE_FNS[stage["key"]](job, db_path)
            stage["status"] = "ok"
            stage["detail"] = detail or ""
            _log(job, f"STAGE {stage['key']} ok: {stage['detail']}")
        job["state"] = "succeeded"
    except StageError as exc:
        _fail(job, str(exc))
    except Exception as exc:  # never let a job die silently
        _fail(job, f"{type(exc).__name__}: {exc}")
    finally:
        job["ended_at"] = _now()
        if job["mode"] == "deploy":
            _record_deploy_state(job, db_path)
        with _LOCK:
            if _ACTIVE.get(job["issue"]) == job["job_id"]:
                del _ACTIVE[job["issue"]]
        _log(job, f"---- job {job['state']} ----")


def _fail(job: dict, message: str) -> None:
    job["state"] = "failed"
    # The reason used to live only in the in-memory job, which a Desk
    # restart forgets. The log is what survives.
    _log(job, f"FAIL {message}")
    for s in job["stages"]:
        if s["status"] == "running":
            s["status"] = "fail"
            s["detail"] = message
            break
    else:
        job["stages"][0]["detail"] = message


def _record_deploy_state(job: dict, db_path) -> None:
    """What production actually carries, kept separate from how the job
    ended. A job that died at the snapshot or build stage never touched
    production, so the previous record stands and the attempt is noted
    beside it; a deploy that went out but failed verification is still a
    deploy, and is recorded as one."""
    deploy_st, verify_st = _stage(job, "deploy"), _stage(job, "verify")
    now = _now()
    with Storage(db_path) as s:
        key = f"deploy_state:{job['issue']}"
        if deploy_st["status"] == "ok":
            verified = verify_st["status"] == "ok"
            record = {"state": "deployed" if verified else "deployed-unverified",
                      "at": now, "url": job["issue_url"],
                      "deployment_id": job["deployment_id"], "verified": verified,
                      "reason": None if verified else verify_st["detail"][:300]}
        elif deploy_st["status"] == "fail":
            record = {"state": "deploy-failed", "at": now, "url": None,
                      "deployment_id": None, "reason": deploy_st["detail"][:300]}
        else:
            prior = s.get_meta(key)
            record = json.loads(prior) if prior else {
                "state": "never-deployed", "at": None, "url": None, "deployment_id": None}
            failed = next((st for st in job["stages"] if st["status"] == "fail"), None)
            record["last_attempt"] = {
                "at": now, "failed_stage": failed["key"] if failed else None,
                "reason": (failed["detail"] if failed else "")[:300]}
        s.set_meta(key, json.dumps(record))


def deploy_state(storage: Storage, league_slug: str, season: str, issue_key: str) -> dict | None:
    raw = storage.get_meta(f"deploy_state:{_issue_key(league_slug, season, issue_key)}")
    return json.loads(raw) if raw else None


# ------------------------------------------------------------------ stages

def _stage_snapshot(job: dict, db_path) -> str:
    from leaguepage import config as cfg
    from leaguepage.publish import (publish_assembled_issue, revise_issue,
                                    snapshot_family)

    league = get_league(job["league_slug"])
    week = (int(job["issue_key"].removeprefix("week-"))
            if job["issue_key"].startswith("week-") else None)
    family = snapshot_family(cfg.PUBLISHED_DIR, league.slug, job["season"], job["issue_key"])
    try:
        with Storage(db_path) as s:
            if job.get("note"):
                if not family:
                    raise StageError("this issue has never been published, so there is "
                                     "nothing to correct; publish it normally")
                snap = revise_issue(s, league, job["season"], job["issue_key"],
                                    note=job["note"], week=week)
                rev = snap.stem.rsplit(".", 1)[-1]
                return f"correction {rev} frozen beside the original: {snap}"
            snap = publish_assembled_issue(s, league, job["season"], job["issue_key"],
                                           week=week)
    except StageError:
        raise
    except Exception as exc:
        raise StageError(f"snapshot blocked: {exc}") from exc
    return f"frozen: {snap}"


def _stage_build(job: dict, db_path) -> str:
    py = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
    proc = _run(job, [str(py if py.exists() else sys.executable),
                      str(REPO_ROOT / "scripts" / "build_public_site.py")],
                cwd=REPO_ROOT, timeout=TIMEOUTS["build"])
    if proc.returncode != 0:
        tail = (proc.stdout + proc.stderr).strip()[-300:]
        raise StageError(f"build/privacy audit failed: {tail}")
    return (proc.stdout or "").strip().splitlines()[-1][:200]


def _vercel_env(job: dict) -> dict:
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
    _log(job, f"deploy env: APPDATA={env.get('APPDATA')!r} "
              f"USERPROFILE={env.get('USERPROFILE')!r} "
              f"HOME={env.get('HOME')!r}")
    _log(job, "auth candidates: " + "; ".join(
        f"{c} -> {'HIT' if (c / 'com.vercel.cli' / 'auth.json').exists() else 'miss'}"
        for c in candidates))
    _log(job, f"pinned XDG_DATA_HOME: {env.get('XDG_DATA_HOME', '(none)')}")
    return env


def _stage_deploy(job: dict, db_path) -> str:
    npx = shutil.which("npx") or shutil.which("npx.cmd")
    if not npx:
        raise StageError("npx not found on PATH; deploy from a terminal instead")
    env = _vercel_env(job)
    who = _run(job, [npx, "--yes", "vercel@latest", "whoami"], cwd=DIST_DIR,
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
            "login` must be run in Jonathan's own terminal to count.")
    link = _run(job, [npx, "--yes", "vercel@latest", "link", "--yes",
                      "--project", VERCEL_PROJECT], cwd=DIST_DIR,
                timeout=TIMEOUTS["link"], env=env)
    if link.returncode != 0:
        raise StageError("vercel link failed: "
                         + (link.stderr or link.stdout).strip()[-250:])
    dep = _run(job, [npx, "--yes", "vercel@latest", "deploy", "--prod", "--yes"],
               cwd=DIST_DIR, timeout=TIMEOUTS["deploy"], env=env)
    out = (dep.stdout or "") + (dep.stderr or "")
    if dep.returncode != 0:
        raise StageError("vercel deploy failed: " + out.strip()[-250:])
    m = re.search(r"/league-page/([A-Za-z0-9]+)", out)
    job["deployment_id"] = f"dpl_{m.group(1)}" if m else None
    return f"deployment {job['deployment_id'] or '(id not reported)'}"


def _probe_url(url: str) -> int:
    """HTTP status of one GET; an HTTP error is its code, anything else 0."""
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(url, timeout=TIMEOUTS["verify"]) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except Exception:
        return 0


def _stage_verify(job: dict, db_path) -> str:
    import time

    paths = ("/", f"/{job['league_slug']}/",
             f"/{job['league_slug']}/{job['season']}/{job['issue_key']}/")
    detail = ""
    for attempt in range(1, VERIFY_ATTEMPTS + 1):
        checks = [(p, _probe_url(PRODUCTION_URL + p)) for p in paths]
        detail = ", ".join(f"{p} -> {c or 'unreachable'}" for p, c in checks)
        if all(c == 200 for _, c in checks):
            return detail + (f" (attempt {attempt})" if attempt > 1 else "")
        _log(job, f"verify attempt {attempt}/{VERIFY_ATTEMPTS}: {detail}")
        if attempt < VERIFY_ATTEMPTS:
            time.sleep(VERIFY_PAUSE)
    raise StageError(f"production verification failed after {VERIFY_ATTEMPTS} attempts: "
                     f"{detail}. The deployment itself went out; check the URLs by hand.")


_STAGE_FNS = {"snapshot": _stage_snapshot, "build": _stage_build,
              "deploy": _stage_deploy, "verify": _stage_verify}
