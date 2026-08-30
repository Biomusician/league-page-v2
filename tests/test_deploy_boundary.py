"""Guards on the GitHub/Vercel deployment boundary.

The rules these tests protect (see docs/DEPLOY.md):
  - Vercel production deploys come ONLY from the 'site' branch, which
    carries the audited static artifact; the source tree must never be
    built or served by Vercel.
  - Local runtime/private state (databases, logs, manager mappings,
    generated prep) is excluded from git.
"""
from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _vercel_config(text: str) -> dict:
    cfg = json.loads(text)
    # Vercel rejects unknown top-level keys (including $comment); keep the
    # file to exactly what the schema allows.
    assert set(cfg) <= {"git", "cleanUrls", "trailingSlash", "headers",
                        "redirects", "rewrites"}
    return cfg


def test_main_vercel_json_disables_source_deploys():
    cfg = _vercel_config((REPO / "vercel.json").read_text(encoding="utf-8"))
    assert cfg["git"]["deploymentEnabled"]["main"] is False


def test_site_branch_vercel_json_disables_main_too():
    from scripts.push_site_branch import VERCEL_JSON

    cfg = _vercel_config(VERCEL_JSON)
    assert cfg["git"]["deploymentEnabled"]["main"] is False


def test_gitignore_excludes_private_runtime_state():
    ignored = (REPO / ".gitignore").read_text(encoding="utf-8").splitlines()
    for required in ("data/", "logs/", "dist/", "dist-preview/",
                     "editorial/managers.json", "editorial/**/PREP.md",
                     ".site-worktree/", ".env"):
        assert required in ignored, f".gitignore must exclude {required}"


def test_repo_audit_patterns_catch_known_shapes():
    from scripts.audit_repo_privacy import FORBIDDEN_PATHS, TOKEN_PATTERNS

    for bad in ("data/league.sqlite3", ".env", "logs/desk-startup.log",
                "editorial/managers.json", "backup.bundle",
                "data/yahoo_token.json"):
        assert FORBIDDEN_PATHS.search(bad), f"should flag path {bad}"
    for ok in ("leaguepage/site_build.py", "docs/DEPLOY.md",
               "editorial/managers.example.json"):
        assert not FORBIDDEN_PATHS.search(ok), f"should not flag {ok}"
    samples = {
        "GitHub token": "ghp_" + "a" * 30,
        "JWT": "eyJ" + "a" * 25 + ".eyJmore",
        "AWS key": "AKIA" + "A" * 16,
    }
    for label, sample in samples.items():
        assert any(p.search(sample) for p, _ in TOKEN_PATTERNS), label
