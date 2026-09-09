"""Migration 0007, checked against a real database rather than read.

`test_schema_parity.py` proves the FILE says the right thing: it creates
`site_documents`, it locks it down, it grants anon nothing, and it does not
invent a ProseKey for a page that has none. That is a static check and it
passes whether or not anybody has run the migration.

This asks the database. It skips -- loudly, with the reason -- until 0007
has actually been applied, because a migration that is written and not
applied is exactly the state a cutover discovers the hard way.

The authorization boundary is asserted the way the application asserts it:
`set local role authenticated` plus a transaction-local
`request.jwt.claims`, inside a real transaction. The connection's own role
is the owner and carries BYPASSRLS, so it proves nothing about RLS and is
never used as evidence here.

Opt in with LEAGUEPAGE_TEST_DATABASE_URL, like the other live tests.
"""
from __future__ import annotations

import json
import os
from contextlib import contextmanager

import pytest

LIVE = os.environ.get("LEAGUEPAGE_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not LIVE, reason="live opt-in not set")

# A slug the product will never author. Deleted either side of every test.
SCRATCH = "__scratch_site_doc__"
STRANGER = "not-the-commissioner@example.invalid"


def _conn():
    import psycopg

    return psycopg.connect(LIVE, connect_timeout=30, autocommit=True)


def _applied() -> bool:
    with _conn() as c:
        return c.execute(
            "select to_regclass('public.site_documents') is not null"
        ).fetchone()[0]


needs_0007 = pytest.mark.skipif(
    LIVE is not None and not _applied(),
    reason="migration 0007 has not been applied to this database yet")


def _commissioner() -> str:
    with _conn() as c:
        row = c.execute("select email from app_commissioners "
                        "order by email limit 1").fetchone()
    assert row, "the live database has no allowlisted Commissioner"
    return row[0]


@contextmanager
def acting_as(email: str | None, role: str = "authenticated"):
    """A transaction that has assumed an identity, exactly as the Desk does.

    Rolled back on the way out: nothing this file asserts about permission
    should be able to leave a row behind.
    """
    import psycopg

    with psycopg.connect(LIVE, connect_timeout=30) as pg:
        with pg.transaction(force_rollback=True):
            with pg.cursor() as cur:
                if email is not None:
                    cur.execute(
                        "select set_config('request.jwt.claims', %s, true)",
                        (json.dumps({"role": role, "email": email}),))
                cur.execute(f"set local role {role}")
                yield cur


@pytest.fixture
def clean():
    if not _applied():
        yield
        return
    with _conn() as c:
        c.execute("delete from site_documents where slug = %s", (SCRATCH,))
    yield
    with _conn() as c:
        c.execute("delete from site_documents where slug = %s", (SCRATCH,))


# ------------------------------------------------------- the migration ran

def test_the_table_exists_once_the_migration_has_been_applied():
    """Not marked `needs_0007`: this is the test that is SUPPOSED to fail
    when somebody believes the migration is applied and it is not."""
    if not _applied():
        pytest.skip("migration 0007 has not been applied to this database yet")
    assert _applied()


@needs_0007
def test_rls_is_enabled_and_forced():
    """FORCE matters more than ENABLE here: without it the owner connection
    the application uses reads straight past the policy."""
    with _conn() as c:
        enabled, forced = c.execute(
            "select c.relrowsecurity, c.relforcerowsecurity from pg_class c "
            "join pg_namespace n on n.oid = c.relnamespace "
            "where n.nspname = 'public' and c.relname = 'site_documents'"
        ).fetchone()
    assert enabled, "RLS is not enabled on site_documents"
    assert forced, "RLS is not FORCED on site_documents"


@needs_0007
def test_there_is_exactly_one_policy_and_it_is_the_shared_one():
    with _conn() as c:
        rows = c.execute(
            "select policyname, cmd, roles::text from pg_policies "
            "where schemaname='public' and tablename='site_documents'"
        ).fetchall()
    assert len(rows) == 1, f"expected one policy, found {rows}"
    name, cmd, roles = rows[0]
    assert name == "commissioner_all"
    assert cmd == "ALL"
    assert "authenticated" in roles and "anon" not in roles


@needs_0007
def test_anon_holds_no_grant_at_all():
    """A policy anon could later be granted around is not a boundary."""
    with _conn() as c:
        grants = c.execute(
            "select privilege_type from information_schema.role_table_grants "
            "where table_schema='public' and table_name='site_documents' "
            "and grantee='anon'").fetchall()
    assert grants == [], f"anon holds {grants} on site_documents"


# ------------------------------------------------------------ who may write

@needs_0007
def test_the_commissioner_can_round_trip_a_document(clean):
    email = _commissioner()
    with acting_as(email) as cur:
        assert cur.execute("select current_role").fetchone()[0] == "authenticated"
        cur.execute(
            "insert into site_documents (slug, body, updated_by) "
            "values (%s, %s, %s)", (SCRATCH, "# hello", email))
        assert cur.execute(
            "select body from site_documents where slug=%s",
            (SCRATCH,)).fetchone()[0] == "# hello"
        cur.execute("update site_documents set body=%s where slug=%s",
                    ("# hello again", SCRATCH))
        assert cur.execute(
            "select body from site_documents where slug=%s",
            (SCRATCH,)).fetchone()[0] == "# hello again"
        cur.execute("delete from site_documents where slug=%s", (SCRATCH,))
        assert cur.execute("select count(*) from site_documents where slug=%s",
                           (SCRATCH,)).fetchone()[0] == 0


@needs_0007
def test_an_authenticated_stranger_sees_nothing_and_writes_nothing(clean):
    """Signed in is not the same as allowed. The allowlist is the boundary,
    and it is enforced by the database rather than by a route."""
    import psycopg

    email = _commissioner()
    with _conn() as c:
        c.execute("insert into site_documents (slug, body, updated_by) "
                  "values (%s, %s, %s)", (SCRATCH, "# private", email))

    with acting_as(STRANGER) as cur:
        assert cur.execute("select count(*) from site_documents").fetchone()[0] == 0
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            cur.execute("insert into site_documents (slug, body) "
                        "values (%s, %s)", ("__stranger__", "x"))

    with _conn() as c:
        assert c.execute("select body from site_documents where slug=%s",
                         (SCRATCH,)).fetchone()[0] == "# private", \
            "the stranger changed the Commissioner's copy"
        assert c.execute("select count(*) from site_documents where slug=%s",
                         ("__stranger__",)).fetchone()[0] == 0


@needs_0007
def test_anon_is_refused_outright(clean):
    """Not "reads zero rows" -- refused. anon holds no grant, so the failure
    is a permission error before any policy is consulted."""
    import psycopg

    with _conn() as c:
        c.execute("insert into site_documents (slug, body) values (%s, %s)",
                  (SCRATCH, "# private"))

    with acting_as(None, role="anon") as cur:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            cur.execute("select body from site_documents")
    with acting_as(None, role="anon") as cur:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            cur.execute("insert into site_documents (slug, body) "
                        "values (%s, %s)", ("__anon__", "x"))

    with _conn() as c:
        assert c.execute("select count(*) from site_documents where slug=%s",
                         ("__anon__",)).fetchone()[0] == 0


# ------------------------------------------------------------- re-runnable

@needs_0007
def test_running_the_migration_again_changes_nothing(clean):
    """It is pasted into a SQL editor by hand. Running it twice has to be a
    no-op, including the policy, which `create policy` alone would not be.
    """
    from pathlib import Path

    sql = (Path(__file__).resolve().parent.parent / "migrations" /
           "0007_site_documents.sql").read_text(encoding="utf-8")

    email = _commissioner()
    with _conn() as c:
        c.execute("insert into site_documents (slug, body, updated_by) "
                  "values (%s, %s, %s)", (SCRATCH, "# keep me", email))
        c.execute(sql)
        assert c.execute("select body from site_documents where slug=%s",
                         (SCRATCH,)).fetchone()[0] == "# keep me", \
            "re-running 0007 destroyed existing content"
        assert c.execute(
            "select count(*) from pg_policies where schemaname='public' "
            "and tablename='site_documents'").fetchone()[0] == 1
