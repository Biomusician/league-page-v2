"""What actually happened, for a job nobody was watching when it died.

A lease that stops being renewed says one thing only: no worker is running
this job any more. It does not say whether the `vercel deploy` that job
had spawned reached production, and that is the question that matters,
because the answer decides whether the Commissioner should publish again
or go and look at the site.

Nothing here retries, re-deploys, or repairs. It gathers evidence and
reports, and a person decides. Automatically re-running the stage that
may already have changed production is the failure this module exists to
prevent, not a convenience it should offer.

Four sources, in descending order of how much they prove:

1. **The event log.** Durable, written under the lease. If the deploy
   stage never reached `running`, production was never touched, and that
   is the end of it.
2. **The checkpoints.** Written either side of each irreversible stage, so
   an `after` mark for `deploy` means the deployment id came back.
3. **The publish log file.** It survives the process, so the `RUN ...
   vercel deploy` line and its `EXIT` code are still there even when the
   crash happened before anything could be recorded in the database.
4. **Production itself.** The same probe the verify stage uses, on a
   shorter timeout because this one is rendered on a status poll. Weakest
   of the four for this purpose: a 200 says the site is up, not that it
   carries this revision. It is only reached when the first three leave
   the question open, so the ordinary lost job costs no network at all.

A job becomes eligible for this by being declared lost, which happens on
the paths that read job state (`sync_jobs.get_sync_job`,
`publish_jobs.get_job_for`): each calls `reap_expired` on the repository it
already holds, so the Desk can never show a job as running when the
process running it is gone.
"""
from __future__ import annotations

import re
from pathlib import Path

from leaguepage import jobs as jobs_mod

# What the recovery concluded. Ordered from "nothing to worry about" to
# "go and look".
NOTHING, SNAPSHOT_ONLY, DEPLOYED, UNCERTAIN = (
    "nothing-happened", "snapshot-only", "deployed", "deploy-uncertain")

# A status poll is waiting on this, so it is deliberately impatient.
PROBE_TIMEOUT = 5

_DEPLOY_RUN = re.compile(r"^\S+ RUN .*vercel@latest deploy", re.M)
_EXIT = re.compile(r"^\S+ EXIT (-?\d+)", re.M)


def _log_says_deploy_went_out(log_path: Path) -> bool | None:
    """True, False, or None for "the log does not say".

    The log outlives the process that wrote it, so it answers this even
    when the crash beat every database write. It reads the LAST deploy
    invocation, because a retried job appends to the same file.
    """
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    runs = list(_DEPLOY_RUN.finditer(text))
    if not runs:
        return None
    after = text[runs[-1].end():]
    exit_line = _EXIT.search(after)
    if exit_line is None:
        return None                     # started, never reported: uncertain
    return exit_line.group(1) == "0"


def probe(db_path, job_id: str, *, probe_live: bool = True) -> dict:
    """Read the evidence for one job and say what it supports.

    Returns a finding with a verdict, the facts behind it, and what the
    Commissioner can safely do next. Everything in `facts` is something
    that was observed; nothing in it is inferred.
    """
    from leaguepage import publish_jobs

    repo = jobs_mod.SQLiteJobRepository(db_path)
    job = repo.get(job_id)
    if job is None:
        return {"job_id": job_id, "verdict": NOTHING,
                "summary": "no such job", "facts": [], "next": []}
    stages = {s["key"]: s for s in
              jobs_mod.fold_stages(repo.events(job_id))}
    checkpoints = (job.result or {}).get("checkpoints", {})
    facts: list[str] = []

    snapshot = stages.get("snapshot", {})
    # The stage reaching "ok" is the evidence; the checkpoint only says
    # which file. A finding must never read "nothing happened" about a
    # directory that has a new immutable snapshot in it, so the stage
    # status decides and a missing checkpoint costs only the detail.
    frozen = snapshot.get("status") == jobs_mod.OK
    named = (checkpoints.get("snapshot") or {}).get("after")
    if frozen:
        facts.append("a snapshot was frozen locally"
                     + (f": {named}" if named else ""))
    elif snapshot.get("status") in (jobs_mod.PENDING, jobs_mod.SKIPPED):
        facts.append("no snapshot was frozen")

    if job.target_revision is not None:
        facts.append(f"this job was bound to revision {job.target_revision} "
                     "and could not have shipped another")

    # Only a deploy job can have touched production at all.
    if job.mode != "deploy":
        verdict = SNAPSHOT_ONLY if frozen else NOTHING
        return _finding(job, verdict, facts, local_only=True)

    deploy = stages.get("deploy", {})
    if deploy.get("status") in (jobs_mod.PENDING, jobs_mod.SKIPPED):
        facts.append("the deploy stage never started, so production was "
                     "not touched by this job")
        return _finding(job, SNAPSHOT_ONLY if frozen else NOTHING, facts,
                        local_only=True)

    deployment_id = (job.result or {}).get("deployment_id")
    if deploy.get("status") == jobs_mod.OK and deployment_id:
        facts.append(f"the deploy stage completed and reported {deployment_id}")
        return _finding(job, DEPLOYED, facts, local_only=False)

    # The hard case: the deploy stage was running when the job was lost.
    log_verdict = _log_says_deploy_went_out(
        Path(publish_jobs._log_path(job.league_slug, job.issue_key)))
    if log_verdict is True:
        facts.append("the publish log records the deploy command exiting 0, "
                     "so the deployment went out even though the job never "
                     "finished recording it")
        verdict = DEPLOYED
    elif log_verdict is False:
        facts.append("the publish log records the deploy command failing, "
                     "so production kept what it already had")
        verdict = SNAPSHOT_ONLY if frozen else NOTHING
    else:
        facts.append("the publish log shows the deploy command starting and "
                     "never reporting an exit code, so whether it reached "
                     "production cannot be determined from this machine")
        verdict = UNCERTAIN

    if probe_live:
        url = (f"{publish_jobs.PRODUCTION_URL}/{job.league_slug}/"
               f"{job.season}/{job.issue_key}/")
        code = publish_jobs._probe_url(url, timeout=PROBE_TIMEOUT)
        facts.append(f"the published issue URL answers {code or 'nothing'} "
                     "right now (which says the site is up, not which "
                     "revision it carries)")
    return _finding(job, verdict, facts, local_only=False)


def _finding(job: jobs_mod.Job, verdict: str, facts: list[str], *,
             local_only: bool) -> dict:
    summary = {
        NOTHING: "Nothing this job started had any lasting effect.",
        SNAPSHOT_ONLY: "A snapshot was frozen on this machine. Production "
                       "was not changed.",
        DEPLOYED: "The deployment went out. Production carries it, whether "
                  "or not the job lived long enough to say so.",
        UNCERTAIN: "This job may or may not have changed production. Check "
                   "the live site before publishing again.",
    }[verdict]
    nexts = {
        NOTHING: ["Publishing again is safe."],
        SNAPSHOT_ONLY: ["The frozen snapshot is immutable and stays. "
                        "Deploying again ships it."],
        DEPLOYED: ["Re-deploying is safe and ships the same built site.",
                   "The production record was written when the deployment "
                   "went out, so the Desk already reflects it."],
        UNCERTAIN: ["Open the published issue and see which text it carries.",
                    "Re-deploying is safe: it ships the built site again "
                    "rather than freezing a new revision."],
    }[verdict]
    return {"job_id": job.job_id, "verdict": verdict, "summary": summary,
            "facts": facts, "next": nexts, "local_only": local_only,
            "revision": job.target_revision, "state": job.state}
