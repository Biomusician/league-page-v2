"""Publish the audited public site to the 'site' branch on GitHub.

The 'site' branch is the Vercel production branch. It carries ONLY the
output of scripts/build_public_site.py (which fails on any privacy-audit
finding) plus a vercel.json safety rail. Pushing it triggers a Vercel
production deployment; nothing else in the repository ever deploys.

Usage:
    .venv/Scripts/python.exe scripts/push_site_branch.py [commit message]

Steps: fresh audited build -> sync dist/ into a temporary worktree of the
site branch -> commit -> push. If the build or audit fails, nothing is
committed or pushed. If the built site is identical to the branch tip,
nothing is pushed and that is reported.
"""
from __future__ import annotations

import datetime as dt
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from leaguepage.config import DIST_DIR  # noqa: E402

BRANCH = "site"
WORKTREE = REPO / ".site-worktree"

# Vercel rejects unknown keys (even $comment), so the explanation lives here:
# this branch is the audited public artifact, served as-is; main can never
# auto-deploy.
VERCEL_JSON = """{
  "git": {
    "deploymentEnabled": {
      "main": false
    }
  }
}
"""


def _git(*args: str, cwd: Path = REPO, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed:\n{proc.stderr.strip()}")
    return proc


def main() -> int:
    message = " ".join(sys.argv[1:]) or (
        "Publish site " + dt.datetime.now().strftime("%Y-%m-%d %H:%M"))

    py = REPO / ".venv" / "Scripts" / "python.exe"
    build = subprocess.run(
        [str(py if py.exists() else sys.executable),
         str(REPO / "scripts" / "build_public_site.py")],
        cwd=REPO, capture_output=True, text=True)
    print(build.stdout.strip())
    if build.returncode != 0:
        print("Build/privacy audit failed; nothing was pushed.")
        return 1

    # (Re)create the worktree on the site branch. remove+add is simpler and
    # safer than reusing a stale worktree that may point at old history.
    if WORKTREE.exists():
        _git("worktree", "remove", "--force", str(WORKTREE), check=False)
        shutil.rmtree(WORKTREE, ignore_errors=True)
    have_local = _git("rev-parse", "--verify", "--quiet", BRANCH,
                      check=False).returncode == 0
    have_remote = _git("ls-remote", "--exit-code", "origin", BRANCH,
                       check=False).returncode == 0
    if not have_local and have_remote:
        _git("fetch", "origin", f"{BRANCH}:{BRANCH}")
        have_local = True
    if have_local:
        _git("worktree", "add", str(WORKTREE), BRANCH)
        if have_remote:
            _git("pull", "--ff-only", "origin", BRANCH, cwd=WORKTREE)
    else:
        _git("worktree", "add", "--detach", str(WORKTREE))
        _git("checkout", "--orphan", BRANCH, cwd=WORKTREE)

    # Replace worktree contents with the fresh build.
    for child in WORKTREE.iterdir():
        if child.name == ".git":
            continue
        shutil.rmtree(child) if child.is_dir() else child.unlink()
    for child in DIST_DIR.iterdir():
        if child.is_dir():
            shutil.copytree(child, WORKTREE / child.name)
        else:
            shutil.copy2(child, WORKTREE / child.name)
    (WORKTREE / "vercel.json").write_text(VERCEL_JSON, encoding="utf-8")

    _git("add", "-A", cwd=WORKTREE)
    if _git("diff", "--cached", "--quiet", cwd=WORKTREE, check=False).returncode == 0:
        print("Site branch already matches the current build; nothing to push.")
        _git("worktree", "remove", "--force", str(WORKTREE), check=False)
        return 0
    _git("commit", "-m", message, cwd=WORKTREE)
    _git("push", "-u", "origin", BRANCH, cwd=WORKTREE)
    sha = _git("rev-parse", "--short", "HEAD", cwd=WORKTREE).stdout.strip()
    _git("worktree", "remove", "--force", str(WORKTREE), check=False)
    print(f"Pushed {BRANCH} @ {sha}: Vercel will deploy it to production.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
