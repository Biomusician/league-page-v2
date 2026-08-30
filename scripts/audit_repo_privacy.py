"""Audit git-TRACKED files for private material before pushing main.

This is the source-repo counterpart of the public-site audit in
site_build.audit_output (which checks dist/). Run it before any push of
main to GitHub:

    .venv/Scripts/python.exe scripts/audit_repo_privacy.py            # HEAD
    .venv/Scripts/python.exe scripts/audit_repo_privacy.py --history  # all commits

Checks every tracked file for:
  - private Sleeper handles (loaded from local editorial/managers.json,
    which is itself gitignored and never printed by this script);
  - credential patterns (GitHub/JWT/AWS tokens, private keys);
  - forbidden tracked paths (databases, .env, logs, bundles, managers.json).

The verbatim historical archive (archive/) is exempt from the handle check:
those newsletters really were written with handles and are deliberately
public. Exit code is non-zero on any finding; nothing sensitive is echoed.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

TOKEN_PATTERNS = [
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"), "GitHub token"),
    (re.compile(r"eyJ[A-Za-z0-9_-]{20,}\.eyJ"), "JWT"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "AWS key"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "private key"),
    (re.compile(r"sk-ant-[A-Za-z0-9-]{10,}"), "Anthropic key"),
]
FORBIDDEN_PATHS = re.compile(
    r"(\.sqlite3$|^\.env|/\.env|\.log$|^logs/|\.bundle$"
    r"|editorial/managers\.json$|auth\.json$|yahoo_token)")
HANDLE_EXEMPT = ("archive/",)


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True,
                          text=True, encoding="utf-8", errors="replace",
                          check=True).stdout


def _handles() -> list[str]:
    mpath = REPO / "editorial" / "managers.json"
    if not mpath.exists():
        raise SystemExit("editorial/managers.json not found; cannot audit "
                         "for handles. Do not push without it.")
    data = json.loads(mpath.read_text(encoding="utf-8"))
    # The commissioner's own public account name is not private.
    return [h for h in data if h.lower() != "biomusician"]


def audit(revs: list[str]) -> list[str]:
    handles = _handles()
    violations: list[str] = []
    for rev in revs:
        files = _git("ls-tree", "-r", "--name-only", rev).splitlines()
        for path in files:
            if FORBIDDEN_PATHS.search(path):
                violations.append(f"{rev[:10]}: forbidden path tracked: {path}")
        blobs = {p: _git("show", f"{rev}:{p}") for p in files
                 if not p.endswith((".jpg", ".png", ".gif", ".ico"))}
        for path, text in blobs.items():
            low = text.lower()
            if not path.startswith(HANDLE_EXEMPT):
                for h in handles:
                    if h.lower() in low:
                        violations.append(
                            f"{rev[:10]}: private handle ({h[:3]}***) in {path}")
            for pat, label in TOKEN_PATTERNS:
                if pat.search(text):
                    violations.append(f"{rev[:10]}: {label} pattern in {path}")
    return violations


def main() -> int:
    if "--history" in sys.argv:
        # The site branch carries rendered PUBLIC output and is audited by
        # site_build.audit_output at build time with the right exemptions
        # (verbatim archive, public team names that happen to match a
        # handle). This audit covers the SOURCE history only.
        revs = _git("rev-list", "--exclude=refs/heads/site",
                    "--exclude=refs/remotes/*/site", "--all").split()
    else:
        revs = ["HEAD"]
    violations = audit(revs)
    if violations:
        print(f"REPO PRIVACY AUDIT FAILED - {len(violations)} finding(s):")
        seen = set()
        for v in violations:
            if v not in seen:
                seen.add(v)
                print(f"  {v}")
        print("Do not push until these are resolved.")
        return 1
    scope = "all reachable history" if "--history" in sys.argv else "HEAD"
    print(f"repo privacy audit clean ({scope}, {len(revs)} commit(s)).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
