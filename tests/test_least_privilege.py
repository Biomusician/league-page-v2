"""What the migrations grant, and what the database actually grants.

Every migration since 0001 says the same four words -- select, insert,
update, delete -- and none of them said what NOT to grant. A new Supabase
project ships default privileges handing `anon`, `authenticated` and
`service_role` ALL privileges on every table created in `public`. The
migrations revoked anon. Nothing revoked the surplus from `authenticated`,
so on 2026-09-09 all 22 tables carried TRUNCATE, REFERENCES and TRIGGER for
the role the application actually writes as.

TRUNCATE is the one worth a test of its own: **row level security does not
apply to it**. `commissioner_all` stops a non-Commissioner deleting one row
and would not stop the same role emptying the table.

Migration 0008 closes it. The live half of this file skips, with the
reason, until 0008 has been applied -- a migration that is written and not
applied is the state a cutover discovers the hard way.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
ZERO_EIGHT = REPO / "migrations" / "0008_least_privilege.sql"

CRUD = {"SELECT", "INSERT", "UPDATE", "DELETE"}

LIVE = os.environ.get("LEAGUEPAGE_TEST_DATABASE_URL")


# ------------------------------------------------------------ static checks

def test_zero_eight_states_the_whole_intended_set_rather_than_the_surplus():
    """Naming privileges to REMOVE means keeping that list in step with the
    server version; MAINTAIN arrived in PostgreSQL 17. Stating the intended
    set is idempotent and version-independent."""
    sql = ZERO_EIGHT.read_text(encoding="utf-8").lower()
    assert "revoke all on public.%i from authenticated" in sql
    assert "grant select, insert, update, delete on public.%i to authenticated" in sql
    assert "revoke truncate" not in sql, "do not enumerate the surplus"


def test_zero_eight_also_fixes_the_defaults_so_the_next_table_is_clean():
    """Without this, the next `create table` re-acquires the surplus and
    0009 inherits the same defect."""
    sql = ZERO_EIGHT.read_text(encoding="utf-8").lower()
    assert "alter default privileges in schema public" in sql
    assert "revoke all on tables from authenticated" in sql
    assert "revoke all on tables from anon" in sql
    assert "grant select, insert, update, delete on tables to authenticated" in sql


def test_zero_eight_touches_no_data_and_drops_nothing():
    sql = ZERO_EIGHT.read_text(encoding="utf-8").lower()
    for destructive in ("drop table", "delete from", "truncate table",
                        "drop column", "alter column", "drop policy"):
        assert destructive not in sql, f"0008 must not {destructive}"


def test_zero_eight_asserts_rather_than_hoping():
    sql = ZERO_EIGHT.read_text(encoding="utf-8").lower()
    assert "raise exception 'least privilege not achieved" in sql
    assert "anon-still-granted" in sql


def test_zero_eight_covers_every_table_not_a_list_that_can_go_stale():
    """0006 and 0007 name their tables in an array, which is right for a
    migration that creates them. This one is about tables that already
    exist, so it reads them from the catalogue instead."""
    sql = ZERO_EIGHT.read_text(encoding="utf-8").lower()
    assert "from pg_class c" in sql and "relkind = 'r'" in sql
    assert not re.search(r"array\s*\[\s*'issues'", sql)


# -------------------------------------------------------------- live checks

def _surplus() -> dict[str, list[str]]:
    """{table: privileges authenticated holds beyond CRUD}."""
    import psycopg

    out: dict[str, list[str]] = {}
    with psycopg.connect(LIVE, connect_timeout=30, autocommit=True) as pg:
        for (t,) in pg.execute(
                "select c.relname from pg_class c "
                "join pg_namespace n on n.oid = c.relnamespace "
                "where n.nspname='public' and c.relkind='r' "
                "order by c.relname").fetchall():
            g = {r[0] for r in pg.execute(
                "select privilege_type from "
                "information_schema.role_table_grants where "
                "table_schema='public' and table_name=%s "
                "and grantee='authenticated'", (t,)).fetchall()}
            if g - CRUD:
                out[t] = sorted(g - CRUD)
    return out


live = pytest.mark.skipif(not LIVE, reason="live opt-in not set")
needs_0008 = pytest.mark.skipif(
    LIVE is not None and bool(_surplus()),
    reason="migration 0008 has not been applied to this database yet")


@live
@needs_0008
def test_authenticated_holds_exactly_crud_on_every_table():
    surplus = _surplus()
    assert surplus == {}, (
        "these tables grant the writing role more than the migrations say: "
        f"{surplus}")


@live
@needs_0008
def test_no_table_grants_authenticated_truncate():
    """Stated separately from the check above because this is the one RLS
    does not cover, and a future change that loosens the grants should fail
    on a test that says why."""
    import psycopg

    with psycopg.connect(LIVE, connect_timeout=30, autocommit=True) as pg:
        rows = pg.execute(
            "select table_name from information_schema.role_table_grants "
            "where table_schema='public' and grantee='authenticated' "
            "and privilege_type='TRUNCATE'").fetchall()
    assert rows == [], (
        f"RLS does not apply to TRUNCATE, and {[r[0] for r in rows]} grant "
        f"it to the role a non-Commissioner signs in as")


@live
@needs_0008
def test_anon_holds_nothing_anywhere():
    import psycopg

    with psycopg.connect(LIVE, connect_timeout=30, autocommit=True) as pg:
        rows = pg.execute(
            "select distinct table_name from "
            "information_schema.role_table_grants "
            "where table_schema='public' and grantee='anon'").fetchall()
    assert rows == [], f"anon holds grants on {[r[0] for r in rows]}"


@live
@needs_0008
def test_a_new_table_would_not_inherit_the_surplus():
    """The defaults, not the tables: this is what stops 0009 reintroducing
    it. Created and dropped inside a rolled-back transaction."""
    import psycopg

    with psycopg.connect(LIVE, connect_timeout=30) as pg:
        with pg.transaction(force_rollback=True):
            pg.execute("create table public.__lp_grant_probe__ (x int)")
            g = {r[0] for r in pg.execute(
                "select privilege_type from "
                "information_schema.role_table_grants where "
                "table_schema='public' and table_name='__lp_grant_probe__' "
                "and grantee='authenticated'").fetchall()}
            anon = pg.execute(
                "select count(*) from information_schema.role_table_grants "
                "where table_schema='public' "
                "and table_name='__lp_grant_probe__' "
                "and grantee='anon'").fetchone()[0]
    assert g == CRUD, f"a new table would grant authenticated {sorted(g)}"
    assert anon == 0, "a new table would grant anon something"


@live
@needs_0008
def test_the_commissioner_can_still_do_the_work(clean_probe_slug=None):
    """Least privilege that breaks the application is not least privilege.
    A full CRUD round trip as the signed-in Commissioner, rolled back."""
    import json

    import psycopg

    with psycopg.connect(LIVE, connect_timeout=30, autocommit=True) as c:
        email = c.execute("select email from app_commissioners "
                          "order by email limit 1").fetchone()[0]

    with psycopg.connect(LIVE, connect_timeout=30) as pg:
        with pg.transaction(force_rollback=True):
            with pg.cursor() as cur:
                cur.execute(
                    "select set_config('request.jwt.claims', %s, true)",
                    (json.dumps({"role": "authenticated", "email": email}),))
                cur.execute("set local role authenticated")
                cur.execute(
                    "insert into site_documents (slug, body, updated_by) "
                    "values (%s, %s, %s)", ("__privilege_probe__", "x", email))
                cur.execute("update site_documents set body='y' where slug=%s",
                            ("__privilege_probe__",))
                assert cur.execute(
                    "select body from site_documents where slug=%s",
                    ("__privilege_probe__",)).fetchone()[0] == "y"
                cur.execute("delete from site_documents where slug=%s",
                            ("__privilege_probe__",))
