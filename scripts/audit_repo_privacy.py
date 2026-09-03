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
sys.path.insert(0, str(REPO))

from leaguepage.privacy import (MIN_HANDLE_LEN, PRIVATE_PATTERNS,  # noqa: E402
                                handle_re, published_matcher)

# Both audits now read one list (leaguepage/privacy.py). They used to keep
# separate ones that disagreed: a Supabase URL or a postgres:// URL was
# blocked from dist/ but committable to main, and an AWS key was the other
# way round. A shape that is private is private in both places.
#
# The email pattern is the one deliberate exception. Tracked source legitimately
# carries addresses (docs, .env.example placeholders, this file's own docstring),
# and the site audit's job of keeping them off public pages is not this one's.
TOKEN_PATTERNS = [(pat, label) for pat, label in PRIVATE_PATTERNS
                  if label not in ("email address", "internal field name",
                                   "absolute path", "private repo path",
                                   "authoring artifact")]
# `.env` and friends are forbidden; `.env.example` carries NAMES ONLY and is
# meant to be committed, so it is explicitly exempt.
# Everything here is gitignored, so only a `git add -f` or an
# already-tracked file reaches this check -- which is exactly the case the
# audit exists to catch.
FORBIDDEN_PATHS = re.compile(
    r"(\.sqlite3$|\.db$|^\.env(?!\.example$)|/\.env(?!\.example$)|\.log$|^logs/"
    r"|^data/|^backups/|^dist/|^dist-preview/"
    r"|commissioner_notes\.md$|/PREP\.md$|/generated/|/dossiers/"
    r"|\.pem$|\.key$|(^|/)id_rsa|\.mbox$"
    r"|\.bundle$|editorial/managers\.json$|auth\.json$|yahoo_token)")
HANDLE_EXEMPT = ("archive/",)
# .env.example exists to document the SHAPE of each setting with <placeholder>
# values and no secrets, so a connection-string template in it is the file
# doing its job. Nothing else is exempt from the credential patterns.
TOKEN_EXEMPT = (".env.example",)


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
    # Keys only was half the file. Aliases and display names are where real
    # first names live, so a real first name committed to main was invisible
    # to this audit while the site audit would have caught it.
    names: set[str] = set()
    for key, m in data.items():
        names.add(key)
        if not isinstance(m, dict):
            continue
        for field in ("display_name", "aliases", "unverified_aliases"):
            v = m.get(field)
            if isinstance(v, str):
                names.add(v)
            elif isinstance(v, list):
                names.update(x for x in v if isinstance(x, str))
    # The commissioner's own public account name is not private. It lives in
    # the file rather than in this script so the exemption cannot drift.
    public = {str(x).lower() for x in (data.get("_public_handles") or [])}
    public.add("biomusician")
    # A nickname a manager put in his own team name is his to publish, and
    # the site audit has always subtracted those. Without the same
    # subtraction, reading aliases here would flag every test fixture that
    # names a real team. The published names come from the local DB, which
    # this script already depends on being present alongside managers.json.
    is_published = published_matcher(_public_names())
    return sorted(h for h in names
                  if h and len(h) >= MIN_HANDLE_LEN
                  and h.lower() not in public and not is_published(h))


def _public_names() -> list[str]:
    """Current public team names, or nothing if the DB is not here.

    Falling back to an empty list is the fail-LOUD direction: the audit then
    flags published nicknames and the operator has to look, rather than
    quietly scanning less than it reports.
    """
    try:
        from leaguepage.config import LEAGUES
        from leaguepage.storage import Storage
        from leaguepage.team_names import resolve_public_names
    except ImportError:
        return []
    out: list[str] = []
    try:
        with Storage() as storage:
            for league in LEAGUES:
                for v in resolve_public_names(storage, league).values():
                    if v.get("name"):
                        out.append(v["name"])
    except Exception as exc:                       # noqa: BLE001
        print(f"  note: public team names unavailable ({type(exc).__name__}); "
              "handle scan will not subtract published nicknames")
    return out


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
            if not path.startswith(HANDLE_EXEMPT):
                for h in handles:
                    if handle_re(h).search(text):
                        violations.append(
                            f"{rev[:10]}: private handle ({h[:3]}***) in {path}")
            for pat, label in TOKEN_PATTERNS:
                if path in TOKEN_EXEMPT:
                    break
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
