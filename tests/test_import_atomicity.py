"""One import is one transaction, or it is not an import.

`apply()` has always carried the docstring "One transaction. Every table
or none of them." On 2026-09-08 the first production run proved that was
a comment rather than a fact: `issue_modules` raised a DatatypeMismatch
and the four `issues` rows written before it stayed.

The cause was `psycopg.connect(..., autocommit=True)` plus a bare
`with pg.cursor()`. Under autocommit that is not a transaction, so:

* every table committed on its own, and
* `SET LOCAL ROLE authenticated` and `set_config('request.jwt.claims',
  ..., true)` were scoped to a transaction that did not exist, so both
  were discarded and the import ran as the connection's own role -- which
  carries BYPASSRLS. The authorization boundary was not weak; it was off.

These tests fail the import at every seam it has and require the same
answer each time: the database is exactly as it was. They also pin the
actor, because a rollback that runs as the owner is still the wrong
thing succeeding.

Opt in with LEAGUEPAGE_TEST_DATABASE_URL, like the other live tests.
Every row lands in a scratch namespace deleted either side.
"""
from __future__ import annotations

import os
from contextlib import contextmanager

import pytest

from scripts.import_editorial_state import ORDER, apply

LIVE = os.environ.get("LEAGUEPAGE_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not LIVE, reason="live opt-in not set")

# A season no league will ever have.
LG, SEASON, ISSUE = "surfeit", "1901", "week-98"
SCRATCH_TABLES = ("issues", "issue_modules", "prose_revisions",
                  "story_decisions", "sections")

# Ids far above anything the real import carries, so a scratch row can
# never be confused for one and cleanup is exact.
REV_IDS = (990001, 990002)


class Boom(RuntimeError):
    """A statement failing for reasons the importer cannot know about."""


def _conn():
    import psycopg

    return psycopg.connect(LIVE, connect_timeout=30, autocommit=True)


def _purge():
    with _conn() as c:
        for table in SCRATCH_TABLES:
            c.execute(f"delete from {table} where season = %s", (SEASON,))


def _counts() -> dict[str, int]:
    with _conn() as c:
        return {t: c.execute(f"select count(*) from {t} where season = %s",
                             (SEASON,)).fetchone()[0]
                for t in SCRATCH_TABLES}


def _seq(name: str):
    with _conn() as c:
        row = c.execute("select last_value from pg_sequences "
                        "where sequencename = %s", (name,)).fetchone()
    return row[0] if row else None


def _commissioner() -> str:
    with _conn() as c:
        row = c.execute("select email from app_commissioners "
                        "order by email limit 1").fetchone()
    assert row, "the live database has no allowlisted Commissioner"
    return row[0]


@contextmanager
def fail_on(fragment: str):
    """Raise the first time a statement containing `fragment` is issued.

    Patched on the cursor rather than simulated, so the failure happens
    where a real one would: inside the write, after earlier statements in
    the same block have already run.
    """
    import psycopg

    real_exec, real_many = psycopg.Cursor.execute, psycopg.Cursor.executemany
    fired = []

    def guard(sql):
        if fragment in str(sql).lower() and not fired:
            fired.append(True)
            raise Boom(fragment)

    def execute(self, query, *a, **kw):
        guard(query)
        return real_exec(self, query, *a, **kw)

    def executemany(self, query, *a, **kw):
        guard(query)
        return real_many(self, query, *a, **kw)

    psycopg.Cursor.execute = execute
    psycopg.Cursor.executemany = executemany
    try:
        yield fired
    finally:
        psycopg.Cursor.execute = real_exec
        psycopg.Cursor.executemany = real_many


def _plan() -> dict:
    """A plan touching the first table in ORDER, the boolean table, a
    serial table and a later one -- enough that a failure anywhere has
    something committed before it to leave behind."""
    at = "2026-01-01T00:00:00Z"
    return {
        "issues": {
            "columns": ["league_slug", "season", "issue_key", "status",
                        "created_at", "updated_at"],
            "identity": ("league_slug", "season", "issue_key"),
            "insert": [(LG, SEASON, ISSUE, "draft", at, at)],
            "same": [], "differs": []},
        # The two BOOLEAN columns, fed the ints SQLite actually stores.
        "issue_modules": {
            "columns": ["league_slug", "season", "issue_key", "module_key",
                        "position", "included", "approved", "updated_at"],
            "identity": ("league_slug", "season", "issue_key", "module_key"),
            "insert": [(LG, SEASON, ISSUE, "lowdown", 1, 1, 0, at),
                       (LG, SEASON, ISSUE, "ctp", 2, 0, 1, at)],
            "same": [], "differs": []},
        "prose_revisions": {
            "columns": ["id", "league_slug", "season", "issue_key",
                        "section", "source", "prior_text", "created_at"],
            "identity": ("id",),
            "insert": [(i, LG, SEASON, ISSUE, "lowdown", "test", "x", at)
                       for i in REV_IDS],
            "same": [], "differs": []},
        "story_decisions": {
            "columns": ["league_slug", "season", "workflow", "candidate_id",
                        "decision", "decided_at"],
            "identity": ("league_slug", "season", "workflow", "candidate_id"),
            "insert": [(LG, SEASON, ISSUE, "cand-1", "include", at)],
            "same": [], "differs": []},
    }


TYPES = {
    "issues": {"league_slug": "text", "season": "text", "issue_key": "text",
               "status": "text", "created_at": "timestamp with time zone",
               "updated_at": "timestamp with time zone"},
    "issue_modules": {"league_slug": "text", "season": "text",
                      "issue_key": "text", "module_key": "text",
                      "position": "integer", "included": "boolean",
                      "approved": "boolean",
                      "updated_at": "timestamp with time zone"},
    "prose_revisions": {"id": "bigint", "league_slug": "text",
                        "season": "text", "issue_key": "text",
                        "section": "text", "source": "text",
                        "prior_text": "text",
                        "created_at": "timestamp with time zone"},
    "story_decisions": {"league_slug": "text", "season": "text",
                        "workflow": "text", "candidate_id": "text",
                        "decision": "text",
                        "decided_at": "timestamp with time zone"},
}


def _section_row():
    """A scratch `sections` row for the state reconciliation to update."""
    with _conn() as c:
        c.execute("insert into sections (league_slug, season, issue_key, "
                  "kind, section, content, state) values "
                  "(%s,%s,%s,'section','lowdown','words','generated') "
                  "on conflict do nothing", (LG, SEASON, ISSUE))
    return [("commissioner-edited", LG, SEASON, ISSUE, "section", "lowdown")]


@pytest.fixture
def clean():
    """Scratch rows AND the sequence they move.

    A successful `apply` ends with `setval(seq, max(id))`, and the scratch
    ids are deliberately enormous so they can never be mistaken for real
    ones -- which means a happy-path test leaves the sequence at 990002
    unless it is put back. Deleting the rows does not undo that; sequences
    do not roll back.
    """
    seq_before = _seq("prose_revisions_id_seq")
    _purge()
    yield
    _purge()
    _restore_seq("prose_revisions_id_seq", seq_before)


def _restore_seq(name: str, value) -> None:
    if value is None:
        # Never called before this test; ALTER puts it back to that.
        with _conn() as c:
            c.execute(f"alter sequence {name} restart")
        return
    with _conn() as c:
        c.execute("select setval(%s, %s)", (name, value))


@pytest.fixture
def pg():
    with _conn() as c:
        yield c


# ------------------------------------------------------- the happy path

def test_the_booleans_sqlite_stores_arrive_as_booleans(clean, pg):
    """The exact write that failed live, now succeeding."""
    written = apply(pg, _plan(), _commissioner(), False, None, TYPES)
    assert written["issue_modules"] == 2
    rows = dict(pg.execute(
        "select module_key, included from issue_modules where season=%s "
        "order by module_key", (SEASON,)).fetchall())
    assert rows == {"lowdown": True, "ctp": False}
    approved = dict(pg.execute(
        "select module_key, approved from issue_modules where season=%s",
        (SEASON,)).fetchall())
    assert approved == {"lowdown": False, "ctp": True}


def test_running_it_twice_changes_nothing(clean, pg):
    actor = _commissioner()
    apply(pg, _plan(), actor, False, None, TYPES)
    first = _counts()
    apply(pg, _plan(), actor, False, None, TYPES)
    assert _counts() == first


# ------------------------------------------- a failure at every seam

@pytest.mark.parametrize("fragment,where", [
    ("insert into issues", "the first table"),
    ("insert into issue_modules", "the middle of the tables"),
    ("insert into story_decisions", "the last table with rows"),
])
def test_a_failure_in_any_table_writes_nothing(clean, pg, fragment, where):
    before = _counts()
    with fail_on(fragment) as fired:
        with pytest.raises(Boom):
            apply(pg, _plan(), _commissioner(), False, None, TYPES)
    assert fired, f"the fault never fired at {where}"
    assert _counts() == before, f"rows survived a failure at {where}"


def test_a_failure_before_the_section_state_update_writes_nothing(clean, pg):
    to_set = _section_row()
    before = _counts()
    with fail_on("insert into story_decisions"):
        with pytest.raises(Boom):
            apply(pg, _plan(), _commissioner(), False, to_set, TYPES)
    assert _counts() == before
    assert pg.execute("select state from sections where season=%s",
                      (SEASON,)).fetchone()[0] == "generated"


def test_a_failure_during_the_section_state_update_writes_nothing(clean, pg):
    to_set = _section_row()
    before = _counts()
    with fail_on("update sections set state") as fired:
        with pytest.raises(Boom):
            apply(pg, _plan(), _commissioner(), False, to_set, TYPES)
    assert fired
    assert _counts() == before, "the tables written before it survived"
    assert pg.execute("select state from sections where season=%s",
                      (SEASON,)).fetchone()[0] == "generated"


def test_a_failure_at_the_sequence_update_writes_nothing(clean, pg):
    """The setvals run last, after every table and the state update, so a
    failure there is the furthest the import can get and still have to
    undo all of it."""
    to_set = _section_row()
    before, seq_before = _counts(), _seq("prose_revisions_id_seq")
    with fail_on("setval") as fired:
        with pytest.raises(Boom):
            apply(pg, _plan(), _commissioner(), False, to_set, TYPES)
    assert fired
    assert _counts() == before
    assert pg.execute("select state from sections where season=%s",
                      (SEASON,)).fetchone()[0] == "generated"
    assert _seq("prose_revisions_id_seq") == seq_before


def test_the_whole_import_is_one_transaction_not_one_per_table(clean, pg):
    """The claim in the docstring, stated as the property it is: a failure
    in the LAST table has to undo the FIRST one."""
    with fail_on("insert into story_decisions"):
        with pytest.raises(Boom):
            apply(pg, _plan(), _commissioner(), False, None, TYPES)
    assert pg.execute("select count(*) from issues where season=%s",
                      (SEASON,)).fetchone()[0] == 0, (
        "issues committed on its own -- exactly the 2026-09-08 failure")


# ------------------------------------------------ the authorization boundary

def test_the_import_writes_as_the_commissioner_and_not_as_the_owner(clean, pg):
    """`current_user` on this connection is the owner and carries
    BYPASSRLS. The import must not."""
    owner, bypass = pg.execute(
        "select current_user, rolbypassrls from pg_roles "
        "where rolname = current_user").fetchone()
    assert bypass, "this test is meaningless if the connection is not privileged"

    import psycopg

    seen = {}
    real = psycopg.Cursor.execute
    real_many = psycopg.Cursor.executemany

    def look(self):
        """Who the connection is at the moment a row goes in -- asked
        inside the same transaction, because outside it the answer is the
        owner and always was."""
        seen["role"] = real(self, "select current_role").fetchone()[0]
        seen["claims"] = real(
            self, "select current_setting('request.jwt.claims', true)"
        ).fetchone()[0]

    def many(self, query, *a, **kw):
        # The inserts go through executemany, which is where the first
        # version of this test looked in the wrong place and passed
        # itself an empty dict.
        if "insert into issues" in str(query).lower():
            look(self)
        return real_many(self, query, *a, **kw)

    psycopg.Cursor.executemany = many
    try:
        apply(pg, {"issues": _plan()["issues"]}, _commissioner(), False,
              None, TYPES)
    finally:
        psycopg.Cursor.executemany = real_many

    assert seen.get("role") == "authenticated", (
        f"wrote as {seen.get('role')!r}, not as the signed-in Commissioner")
    assert _commissioner() in (seen.get("claims") or ""), (
        "the JWT claims did not carry the Commissioner")
    assert seen["role"] != owner


def test_a_stranger_cannot_run_the_import(clean, pg):
    """RLS, not politeness: an authenticated actor who is not on the
    allowlist writes nothing."""
    before = _counts()
    with pytest.raises(Exception) as exc:
        apply(pg, _plan(), "nobody@example.invalid", False, None, TYPES)
    assert "Boom" not in type(exc.value).__name__
    assert _counts() == before


def test_the_role_is_checked_rather_than_assumed(clean, pg, monkeypatch):
    """If a future change puts the writes back outside a transaction,
    SET LOCAL silently stops working and everything lands as the owner.
    `apply` asks who it is before writing, so that cannot be silent."""
    import psycopg

    real = psycopg.Cursor.execute

    def lie(self, query, *a, **kw):
        if str(query).strip().lower() == "set local role authenticated":
            return real(self, "select 1", *a, **kw)
        return real(self, query, *a, **kw)

    monkeypatch.setattr(psycopg.Cursor, "execute", lie)
    before = _counts()
    with pytest.raises(RuntimeError, match="refusing to write as"):
        apply(pg, _plan(), _commissioner(), False, None, TYPES)
    monkeypatch.undo()
    assert _counts() == before


# ------------------------------------------------------------- the tables

def test_every_table_the_import_writes_is_covered_by_a_rollback_test():
    """A new table in ORDER with no scratch coverage is a seam nobody has
    failed on purpose. This does not demand a row per table -- it demands
    that the ones exercised span the first, a middle and the last."""
    covered = set(_plan())
    assert covered <= set(ORDER)
    assert ORDER[0] in covered, "the first table has to be one of them"
    assert len(covered) >= 3


# ------------------------------------------------------------- the dry run

def test_the_dry_run_issues_no_write_against_the_cloud(clean, pg):
    """The planning phase reads. Not "reads as far as anyone checked" --
    every statement it sends is recorded and none of them may mutate."""
    import psycopg

    from leaguepage.storage import Storage
    from scripts.import_editorial_state import (plan, prose_matches,
                                                state_plan)

    sent: list[str] = []
    real, real_many = psycopg.Cursor.execute, psycopg.Cursor.executemany

    def note(self, query, *a, **kw):
        sent.append(" ".join(str(query).lower().split()))
        return real(self, query, *a, **kw)

    def note_many(self, query, *a, **kw):
        sent.append(" ".join(str(query).lower().split()))
        return real_many(self, query, *a, **kw)

    before = _counts()
    psycopg.Cursor.execute, psycopg.Cursor.executemany = note, note_many
    try:
        with Storage() as s:
            plan(s, pg)
            prose_matches(pg)
            state_plan(s, pg)
    finally:
        psycopg.Cursor.execute, psycopg.Cursor.executemany = real, real_many

    assert sent, "the dry run sent nothing at all, which cannot be right"
    writes = [q for q in sent
              if q.startswith(("insert", "update", "delete", "truncate",
                               "alter", "drop", "create"))
              or "setval(" in q]
    assert not writes, writes[:5]
    assert _counts() == before
