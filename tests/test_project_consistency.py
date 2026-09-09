"""Auth and the database have to be the same Supabase project.

On 2026-09-09 they were not, and had not been for weeks. `DATABASE_URL`
pointed at the project holding every migration and all 842 imported
editorial rows; `SUPABASE_URL` pointed at a different project carrying
only 0001's tables, and that is where sign-in happened.

Nothing broke, and it is worth being exact about why, because the obvious
reading is wrong. `app_is_commissioner()` reads `auth.jwt() ->> 'email'`,
and that value is the one THIS APPLICATION asserts from its own session --
the database never verifies a Supabase-issued token. So the mismatch let
nobody past RLS.

What it did mean is that the allowlist deciding access and the accounts
vouching for people lived in two projects with nothing linking them, and
that a diagnostic explained six cross-project misses as a stale schema
cache for weeks.

No test could have caught it, because nothing compared the two. This is
that test.

Nothing here touches a network or a real credential: every case is a
synthetic DSN with a fake password, so the file also demonstrates the
property it asserts -- a project check does not need to see a secret.
"""
from __future__ import annotations

import pytest

from leaguepage import project_check as pc

# Deliberately unmistakable. A real project ref must never be
# committed, and this file is exempt from the credential
# patterns precisely because everything in it is invented --
# `test_repo_privacy_exemptions.py` proves that stays true.
REF_A = "aaaaaaaaaaaaaaaaaaaa"
REF_B = "bbbbbbbbbbbbbbbbbbbb"

POOLER = f"postgresql://postgres.{REF_A}:pw@aws-0-us-east-2.pooler.supabase.com:5432/postgres"
DIRECT = f"postgresql://postgres:pw@db.{REF_A}.supabase.co:5432/postgres"
API_A = f"https://{REF_A}.supabase.co"
API_B = f"https://{REF_B}.supabase.co"


# --------------------------------------------------------------- parsing

def test_the_pooler_hides_the_ref_in_the_username():
    """The shape this project actually uses. A hostname-only check would
    call every pooled project `unknown` and pass a mismatch through."""
    assert pc.ref_of_dsn(POOLER) == REF_A


def test_the_direct_host_carries_it_in_the_hostname():
    assert pc.ref_of_dsn(DIRECT) == REF_A


def test_the_api_url_carries_it_as_the_subdomain():
    assert pc.ref_of_url(API_A) == REF_A
    assert pc.ref_of_url(API_A + "/") == REF_A


@pytest.mark.parametrize("dsn", [
    "postgresql://someone:pw@db.example.com:5432/app",       # self-hosted
    "postgresql://postgres:pw@127.0.0.1:5432/postgres",      # local
    "postgres://postgres@/var/run/postgresql",               # socket
    "not a url at all",
    "",
    None,
])
def test_a_dsn_with_no_ref_is_none_rather_than_a_guess(dsn):
    assert pc.ref_of_dsn(dsn) is None


# --------------------------------------------------------------- verdicts

def test_the_same_project_matches_through_the_pooler():
    d = pc.describe(POOLER, API_A)
    assert d["verdict"] == pc.MATCH
    assert d["database_ref"] == d["auth_ref"] == REF_A


def test_the_configuration_found_on_2026_09_09_is_a_mismatch():
    d = pc.describe(POOLER, API_B)
    assert d["verdict"] == pc.MISMATCH
    assert d["database_ref"] == REF_A and d["auth_ref"] == REF_B


def test_a_self_hosted_dsn_is_unknown_and_not_a_mismatch():
    """Refusing to start over a DSN this module cannot parse would be the
    module inventing a policy it was not given."""
    d = pc.describe("postgresql://me:pw@db.example.com/app", API_A)
    assert d["verdict"] == pc.UNKNOWN


@pytest.mark.parametrize("dsn,url", [(None, API_A), (POOLER, None),
                                     (None, None), ("", "")])
def test_absent_configuration_is_not_a_failure(dsn, url):
    """An explicit None means "there is none", not "go and look".

    Those were the same thing until the full suite ran this file with a
    live DSN in the environment and one case answered `match`. A check
    whose answer depends on which tests ran first is not a check.
    """
    assert pc.describe(dsn, url)["verdict"] == pc.ABSENT


def test_calling_it_with_no_arguments_reads_the_configuration(monkeypatch):
    from leaguepage import settings

    monkeypatch.setattr(settings, "get", lambda name, default=None: {
        settings.DATABASE_URL: POOLER, settings.SUPABASE_URL: API_B,
    }.get(name, default))
    assert pc.describe()["verdict"] == pc.MISMATCH


# ------------------------------------------------------------- the message

def test_the_message_names_the_projects_and_nothing_else():
    """A project ref is public. A DSN is SECRET-class, and none of it --
    host, user, password, port -- may appear in an error a log will keep."""
    msg = pc.problem(POOLER, API_B)
    assert REF_A in msg and REF_B in msg
    for secret in ("pw", "postgresql://", "pooler.supabase.com", "5432",
                   "postgres."):
        assert secret not in msg, f"the mismatch message leaked {secret!r}"


def test_there_is_no_message_when_there_is_no_mismatch():
    assert pc.problem(POOLER, API_A) == ""
    assert pc.problem(None, None) == ""


# ------------------------------------------------------------- enforcement

def test_a_postgres_backend_refuses_to_start_on_a_mismatch():
    with pytest.raises(RuntimeError, match="Supabase project mismatch"):
        pc.enforce(backend="postgres", auth_required=False,
                   dsn=POOLER, url=API_B)


def test_sign_in_refuses_to_start_on_a_mismatch():
    with pytest.raises(RuntimeError, match="Supabase project mismatch"):
        pc.enforce(backend="filesystem", auth_required=True,
                   dsn=POOLER, url=API_B)


def test_the_local_filesystem_desk_warns_rather_than_refusing():
    """The configuration on this machine today. The two halves never meet
    here -- nobody signs in and nothing reads Postgres -- and a config
    error an operator cannot act on without stopping work is one that gets
    worked around."""
    warning = pc.enforce(backend="filesystem", auth_required=False,
                         dsn=POOLER, url=API_B)
    assert warning.startswith("Supabase project mismatch")


def test_a_matching_configuration_says_nothing_at_all():
    for backend in ("filesystem", "postgres"):
        for required in (True, False):
            assert pc.enforce(backend=backend, auth_required=required,
                              dsn=POOLER, url=API_A) == ""


# ------------------------------------------------------------- the surface

def test_health_reports_the_verdict_and_never_a_ref():
    """/health is the one route a launcher reads without signing in."""
    from pathlib import Path

    from fastapi.testclient import TestClient

    from leaguepage import settings
    from leaguepage.desk import create_app
    from leaguepage.storage import Storage

    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "t.sqlite3"
        with Storage(db):
            pass
        body = TestClient(create_app(db_path=db)).get("/health").json()

    assert body["supabase_projects"] in {pc.MATCH, pc.MISMATCH, pc.UNKNOWN,
                                         pc.ABSENT}
    text = repr(body)
    for leak in ("supabase.co", "postgresql://", "pooler", "DATABASE_URL"):
        assert leak not in text, f"/health leaked {leak!r}"
    real = settings.get(settings.SUPABASE_URL) or ""
    if real:
        assert (pc.ref_of_url(real) or "zzz") not in text
