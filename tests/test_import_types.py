"""SQLite's 0 is not Postgres's false, and the import has to know that.

The first production import attempt died here:

    psycopg.errors.DatatypeMismatch: column "included" is of type boolean
    but expression is of type smallint

SQLite has no boolean type. It keeps 0 and 1 in an INTEGER column and
hands them back as Python ints, and psycopg -- correctly -- does not guess
that an int was meant as a truth value. The failure was right; what was
wrong is that it arrived as an exception in the middle of a write instead
of a refusal before one.

Two things are pinned here. The conversion, which is narrow on purpose:
0 and 1 and nothing else, because coercing by truthiness would turn a 2,
an empty string or the word "false" into an answer this script invented
about data nobody has looked at. And the preflight, which walks every
value that would be written against the column it would land in, so a
type problem is a refusal with a column name on it.
"""
from __future__ import annotations

import pytest

from scripts.import_editorial_state import (ACCEPTS, SHOWABLE, as_bool,
                                            normalize, preflight)

BOOL_TABLE = "issue_modules"


def _plan(table, columns, rows, identity=("league_slug",), differs=()):
    return {table: {"columns": list(columns), "identity": identity,
                    "insert": list(rows), "same": [],
                    "differs": list(differs)}}


# ------------------------------------------------------------ conversion

@pytest.mark.parametrize("given,want", [(0, False), (1, True),
                                        (False, False), (True, True),
                                        (None, None)])
def test_the_values_sqlite_actually_stores(given, want):
    assert as_bool(given) is want


@pytest.mark.parametrize("given", [2, -1, "1", "true", "false", "", "yes", 1.0])
def test_anything_else_is_refused_rather_than_guessed(given):
    """Truthiness would answer all of these. None of them is a fact about
    the column; each is a fact about data nobody has looked at."""
    with pytest.raises(ValueError):
        as_bool(given)


def test_normalize_converts_only_the_boolean_columns():
    cols = ["league_slug", "position", "included", "custom_title"]
    types = {"league_slug": "text", "position": "integer",
             "included": "boolean", "custom_title": "text"}
    out = normalize([("disco", 3, 1, "Title"), ("surfeit", 0, 0, None)],
                    cols, types)
    assert out == [("disco", 3, True, "Title"), ("surfeit", 0, False, None)]


def test_normalize_leaves_a_table_with_no_booleans_untouched():
    """Identity, not a copy that happens to be equal: nothing should be
    reformatted on the way past."""
    rows = [("disco", "2026", "week-01")]
    types = {"league_slug": "text", "season": "text", "issue_key": "text"}
    assert normalize(rows, ["league_slug", "season", "issue_key"], types) is rows


def test_a_null_boolean_survives_where_the_column_allows_it():
    types = {"approved_sha": "text", "approved": "boolean"}
    assert normalize([("abc", None)], ["approved_sha", "approved"],
                     types) == [("abc", None)]


# ------------------------------------------------------------- preflight

def test_preflight_passes_the_rows_that_broke_the_first_attempt():
    """The exact shape that failed live: SQLite ints bound to BOOLEAN."""
    p = _plan(BOOL_TABLE, ["league_slug", "included", "approved"],
              [("disco", 1, 0), ("surfeit", 0, 1)])
    types = {BOOL_TABLE: {"league_slug": "text", "included": "boolean",
                          "approved": "boolean"}}
    assert preflight(p, types) == []


def test_preflight_refuses_a_boolean_it_cannot_read():
    p = _plan(BOOL_TABLE, ["league_slug", "included"], [("disco", 7)])
    types = {BOOL_TABLE: {"league_slug": "text", "included": "boolean"}}
    problems = preflight(p, types)
    assert len(problems) == 1
    assert "issue_modules.included" in problems[0]
    assert "boolean" in problems[0] and "7" in problems[0]


def test_preflight_names_the_column_and_the_destination_type():
    p = _plan("takes", ["take_id", "created_at"], [("t1", 17)])
    types = {"takes": {"take_id": "text",
                       "created_at": "timestamp with time zone"}}
    problems = preflight(p, types)
    assert len(problems) == 1
    assert "takes.created_at" in problems[0]
    assert "timestamp with time zone" in problems[0]
    assert "int" in problems[0]


def test_preflight_never_prints_what_a_text_column_holds():
    """A text column may be prose, a baseline draft or a private note. A
    type failure is not a reason to put any of it on a terminal."""
    secret = "SECRET-BASELINE-DRAFT-DO-NOT-PRINT"
    p = _plan("prose_revisions", ["id", "prior_text"], [(1, {"t": secret})])
    types = {"prose_revisions": {"id": "integer", "prior_text": "text"}}
    problems = preflight(p, types)
    assert len(problems) == 1
    assert secret not in problems[0]
    assert "prose_revisions.prior_text" in problems[0]
    assert "dict" in problems[0]
    assert "text" not in SHOWABLE


def test_preflight_reports_a_column_once_however_many_rows_repeat_it():
    p = _plan(BOOL_TABLE, ["included"], [(9,)] * 50)
    types = {BOOL_TABLE: {"included": "boolean"}}
    assert len(preflight(p, types)) == 1


def test_preflight_checks_the_rows_an_overwrite_would_replace_too():
    """`differs` rows are written under --overwrite and are just as
    capable of carrying a value the column will not take."""
    p = _plan(BOOL_TABLE, ["included"], [], differs=[((3,), (True,))])
    types = {BOOL_TABLE: {"included": "boolean"}}
    assert len(preflight(p, types)) == 1


def test_preflight_refuses_a_destination_type_it_does_not_know():
    """Silence about an unrecognised type would be the same failure again,
    one column further along."""
    p = _plan("takes", ["payload"], [({"a": 1},)])
    types = {"takes": {"payload": "jsonb"}}
    problems = preflight(p, types)
    assert len(problems) == 1 and "jsonb" in problems[0]
    assert "jsonb" not in ACCEPTS


def test_a_null_needs_no_type_check():
    p = _plan("takes", ["take_id", "resolved_at"], [("t1", None)])
    types = {"takes": {"take_id": "text",
                       "resolved_at": "timestamp with time zone"}}
    assert preflight(p, types) == []


def test_the_timestamps_sqlite_keeps_as_text_are_accepted():
    """Proved by the four `issues` rows that went in on the first attempt:
    Postgres parses the ISO string SQLite stores."""
    p = _plan("issues", ["issue_key", "created_at"],
              [("week-01", "2026-09-05T01:39:00Z")])
    types = {"issues": {"issue_key": "text",
                        "created_at": "timestamp with time zone"}}
    assert preflight(p, types) == []
    assert "str" in ACCEPTS["timestamp with time zone"]


def test_a_bool_is_not_accepted_where_an_integer_is_wanted():
    """`bool` is a subclass of `int` in Python and is not one at a column
    boundary, so the check has to look at bool first."""
    p = _plan("issue_modules", ["position"], [(True,)])
    types = {"issue_modules": {"position": "integer"}}
    problems = preflight(p, types)
    assert len(problems) == 1 and "issue_modules.position" in problems[0]


# ------------------------------------- the guard, without a database

class _FakeCursor:
    """Enough of a psycopg cursor to run `apply` against nothing."""

    def __init__(self, role, log):
        self.role, self.log = role, log

    def execute(self, sql, args=None):
        self.log.append(" ".join(str(sql).lower().split()))
        self._last = [(self.role,)] if "current_role" in str(sql) else [(1,)]
        return self

    def executemany(self, sql, rows):
        self.log.append(" ".join(str(sql).lower().split()))

    def fetchone(self):
        return self._last[0]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConn:
    def __init__(self, role):
        self.role, self.log, self.transactions = role, [], 0

    def transaction(self):
        conn = self

        class _T:
            def __enter__(self):
                conn.transactions += 1
                return self

            def __exit__(self, *exc):
                return False
        return _T()

    def cursor(self):
        return _FakeCursor(self.role, self.log)


PLAN = {"issues": {"columns": ["league_slug"], "identity": ("league_slug",),
                   "insert": [("disco",)], "same": [], "differs": []}}


def test_apply_refuses_to_write_as_anyone_but_the_commissioner():
    """The regression guard for the 2026-09-08 incident.

    Under `autocommit=True` a bare cursor is not a transaction, so
    `SET LOCAL ROLE authenticated` was discarded and every row went in as
    the connection's own role -- which carries BYPASSRLS. Nothing said so.
    `apply` asks who it is before it writes, so if that ever comes back
    the import stops instead of quietly bypassing RLS.
    """
    from scripts.import_editorial_state import apply

    pg = _FakeConn("postgres")
    with pytest.raises(RuntimeError, match="refusing to write as 'postgres'"):
        apply(pg, PLAN, "commish@example.com", False)
    assert not [q for q in pg.log if q.startswith("insert")], \
        "it wrote before checking who it was"


def test_apply_opens_one_explicit_transaction():
    from scripts.import_editorial_state import apply

    pg = _FakeConn("authenticated")
    apply(pg, PLAN, "commish@example.com", False)
    assert pg.transactions == 1, (
        "the write phase has to be inside an explicit transaction; under "
        "autocommit a bare cursor commits each statement on its own")


def test_the_claims_and_the_role_are_set_before_any_insert():
    from scripts.import_editorial_state import apply

    pg = _FakeConn("authenticated")
    apply(pg, PLAN, "commish@example.com", False)
    first_insert = next(i for i, q in enumerate(pg.log)
                        if q.startswith("insert"))
    assert any("request.jwt.claims" in q for q in pg.log[:first_insert])
    assert any("set local role authenticated" == q
               for q in pg.log[:first_insert])
