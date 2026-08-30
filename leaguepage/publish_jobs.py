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

_LOCK = threading.Lock()
_JOBS: dict[str, dict] = {}          # job_id -> job
_ACTIVE: dict[str, str] = {}         # issue key -> job_id of running job

STAGES_LOCAL = [("snapshot", "Creating immutable issue snapshot"),
                ("build", "Building public site + privacy audit")]
STAGES_DEPLOY = STAGES_LOCAL + [("deploy", "Deploying to Vercel production"),
                                ("verify", "Verifying production URLs")]


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
                      mode: str) -> tuple[dict, bool]:
    """(job, created). A running job for the same issue is returned as-is:
    duplicate clicks can never start duplicate production deployments."""
    key = _issue_key(league_slug, season, issue_key)
    with _LOCK:
        active_id = _ACTIVE.get(key)
        if active_id and _JOBS[active_id]["state"] == "running":
            return _JOBS[active_id], False
        stages = STAGES_DEPLOY if mode == "deploy" else STAGES_LOCAL
        job = {
            "job_id": uuid.uuid4().hex[:12],
            "issue": key,
            "league_slug": league_slug, "season": season, "issue_key": issue_key,
            "mode": mode, "state": "running",
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
    _log(job, f"---- publish job {job['job_id']} mode={mode} ----")
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
    for s in job["stages"]:
        if s["status"] == "running":
            s["status"] = "fail"
            s["detail"] = message
            break
    else:
        job["stages"][0]["detail"] = message


def _record_deploy_state(job: dict, db_path) -> None:
    deployed = job["state"] == "succeeded"
    with Storage(db_path) as s:
        s.set_meta(f"deploy_state:{job['issue']}", json.dumps({
            "state": "deployed" if deployed else "deploy-failed",
            "at": _now(), "url": job["issue_url"] if deployed else None,
            "deployment_id": job["deployment_id"],
        }))


def deploy_state(storage: Storage, league_slug: str, season: str, issue_key: str) -> dict | None:
    raw = storage.get_meta(f"deploy_state:{_issue_key(league_slug, season, issue_key)}")
    return json.loads(raw) if raw else None


# ------------------------------------------------------------------ stages

def _stage_snapshot(job: dict, db_path) -> str:
    from leaguepage.publish import publish_assembled_issue

    league = get_league(job["league_slug"])
    week = (int(job["issue_key"].removeprefix("week-"))
            if job["issue_key"].startswith("week-") else None)
    try:
        with Storage(db_path) as s:
            snap = publish_assembled_issue(s, league, job["season"], job["issue_key"],
                                           week=week)
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
    XDG_DATA_HOME to whichever candidate dir actually holds auth.json."""
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
            "Vercel CLI cannot see its credentials from this Desk process. "
            "Most likely this Desk was started by an automation sandbox that "
            "masks the credential file: close this Desk window, double-click "
            "'Launch Commissioner Desk' yourself, and retry. If it still "
            "fails, run `npx vercel login` in a terminal, then retry.")
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


def _stage_verify(job: dict, db_path) -> str:
    import urllib.request

    checks = []
    for path in ("/", f"/{job['league_slug']}/",
                 f"/{job['league_slug']}/{job['season']}/{job['issue_key']}/"):
        try:
            with urllib.request.urlopen(PRODUCTION_URL + path,
                                        timeout=TIMEOUTS["verify"]) as resp:
                checks.append((path, resp.status))
        except Exception:
            checks.append((path, 0))
    detail = ", ".join(f"{p} -> {c or 'unreachable'}" for p, c in checks)
    if not all(c == 200 for _, c in checks):
        raise StageError(f"production verification failed: {detail}")
    return detail


_STAGE_FNS = {"snapshot": _stage_snapshot, "build": _stage_build,
              "deploy": _stage_deploy, "verify": _stage_verify}
