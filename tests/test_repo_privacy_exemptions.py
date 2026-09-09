"""Whatever is exempt from the credential patterns has to earn it.

`scripts/audit_repo_privacy.py` refuses to let a Supabase URL or a Postgres
DSN into a tracked file. Three files are exempt: `.env.example`, which
documents the SHAPE of each setting with placeholder values, and the two
test files that prove `leaguepage/project_check.py` parses those shapes --
which cannot be tested without something shaped like one.

An exemption is where a real credential eventually hides, so this file
polices the exempted ones. It checks them against the LIVE configuration
rather than against a copy of it, so nothing real is committed here either:
the answer comes from `.env` at runtime and the assertion is an absence.

On a machine with no `.env` these checks skip, and say so. That is the
honest outcome -- there is nothing to compare against -- and the audit
itself still runs everywhere.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def _exempt() -> tuple[str, ...]:
    """Read the tuple from the audit's source rather than importing it: the
    script pulls in the whole package at import time and this needs nothing
    but the list."""
    src = (REPO / "scripts" / "audit_repo_privacy.py").read_text(
        encoding="utf-8")
    body = src[src.index("TOKEN_EXEMPT = ("):]
    body = body[:body.index(")") + 1]
    return tuple(re.findall(r'"([^"]+)"', body))


def _real_refs() -> set[str]:
    """The project refs this machine is actually configured with, read at
    run time and never written down."""
    from leaguepage import project_check, settings

    refs = {project_check.ref_of_dsn(settings.get(settings.DATABASE_URL)),
            project_check.ref_of_url(settings.get(settings.SUPABASE_URL))}
    return {r for r in refs if r}


def _real_password() -> str | None:
    from urllib.parse import urlparse

    from leaguepage import settings

    dsn = settings.get(settings.DATABASE_URL)
    if not dsn:
        return None
    try:
        return urlparse(dsn).password
    except ValueError:
        return None


def test_the_exemption_list_is_short_and_every_entry_exists():
    """An exemption nobody can enumerate is not an exemption, it is a hole."""
    paths = _exempt()
    assert len(paths) <= 4, f"the exemption list is growing: {paths}"
    for rel in paths:
        assert (REPO / rel).exists(), f"exempt path no longer exists: {rel}"


@pytest.mark.parametrize("rel", _exempt())
def test_no_exempt_file_carries_a_real_project_ref(rel):
    refs = _real_refs()
    if not refs:
        pytest.skip("no Supabase configuration on this machine to compare to")
    text = (REPO / rel).read_text(encoding="utf-8")
    for ref in refs:
        assert ref not in text, (
            f"{rel} carries this machine's real Supabase project ref. The "
            f"exemption is for invented values only.")


@pytest.mark.parametrize("rel", _exempt())
def test_no_exempt_file_carries_the_real_database_password(rel):
    password = _real_password()
    if not password or len(password) < 8:
        pytest.skip("no database password on this machine to compare to")
    text = (REPO / rel).read_text(encoding="utf-8")
    assert password not in text, f"{rel} carries the real database password"


def test_the_exempt_test_files_are_the_ones_that_need_the_shapes():
    """Named rather than assumed: these two exist to parse Supabase URLs and
    Postgres DSNs. If a third file is added, somebody has to justify it
    here."""
    paths = set(_exempt())
    assert paths == {
        ".env.example",
        "tests/test_project_consistency.py",
        "tests/test_auth_chain_end_to_end.py",
    }, sorted(paths)
    for rel in ("tests/test_project_consistency.py",
                "tests/test_auth_chain_end_to_end.py"):
        text = (REPO / rel).read_text(encoding="utf-8")
        assert "project_check" in text, (
            f"{rel} is exempt from the credential patterns but does not test "
            f"the module that needs those shapes")
