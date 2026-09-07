"""`Storage.transaction()`: what it promises, and what it refuses to do.

Before Tranche 5A, `Storage._cursor()` committed after every mutating
method, so one Commissioner click was several committed transactions that
happened to share a file. These pin the semantics that replaced it,
including the ones chosen deliberately rather than inherited:

  nesting is by DEPTH, not savepoints -- an inner scope cannot commit the
  outer one's work, and nothing needs an inner rollback

  a failure anywhere poisons the whole scope -- if the body swallows the
  exception, the outermost scope rolls back and raises rather than
  committing half a Commissioner action quietly

  outside a scope nothing changed, so no existing caller had to learn
  about any of this
"""
from __future__ import annotations

import sqlite3

import pytest

from leaguepage.storage import Storage, TransactionAborted


@pytest.fixture
def s(tmp_path):
    with Storage(tmp_path / "tx.sqlite3") as store:
        yield store


def _commits(store) -> list[str]:
    """Every COMMIT and ROLLBACK the connection actually issues."""
    seen: list[str] = []
    store._conn.set_trace_callback(          # noqa: SLF001 - the point of the test
        lambda sql: seen.append(sql.strip().split()[0].upper())
        if sql.strip().split()[0].upper() in ("COMMIT", "ROLLBACK") else None)
    return seen


# ------------------------------------------------------------ the basics

def test_outside_a_scope_one_method_is_still_one_transaction(s):
    """The compatibility promise: nothing that already worked changed."""
    seen = _commits(s)
    s.set_meta("a", "1")
    s.set_meta("b", "2")
    assert seen.count("COMMIT") == 2
    assert s.get_meta("a") == "1" and s.get_meta("b") == "2"


def test_inside_a_scope_the_writes_commit_once(s):
    seen = _commits(s)
    with s.transaction():
        s.set_meta("a", "1")
        s.set_meta("b", "2")
        s.set_meta("c", "3")
        assert "COMMIT" not in seen, "nothing commits while the scope is open"
    assert seen.count("COMMIT") == 1
    assert s.get_meta("c") == "3"


def test_an_exception_rolls_the_whole_scope_back(s):
    s.set_meta("before", "kept")
    with pytest.raises(ValueError):
        with s.transaction():
            s.set_meta("a", "1")
            s.set_meta("b", "2")
            raise ValueError("something went wrong halfway")
    assert s.get_meta("a") is None, "the first write went too"
    assert s.get_meta("b") is None
    assert s.get_meta("before") == "kept", "and nothing before it was touched"


def test_a_failure_at_the_first_write_leaves_nothing(s):
    with pytest.raises(sqlite3.Error):
        with s.transaction():
            s._conn.execute("INSERT INTO issues (league_slug) VALUES ('x')")
    assert s.list_issues("x") == []


def test_a_failure_at_the_last_write_undoes_the_first(s):
    with pytest.raises(sqlite3.Error):
        with s.transaction():
            s.set_meta("first", "written")
            s._conn.execute("INSERT INTO issues (league_slug) VALUES ('x')")
    assert s.get_meta("first") is None


# --------------------------------------------------------------- nesting

def test_an_inner_scope_cannot_commit_the_outer_ones_work(s):
    """Helpers call helpers. The depth counter is what stops an inner
    scope ending the outer transaction early."""
    seen = _commits(s)
    with s.transaction():
        s.set_meta("outer", "1")
        with s.transaction():
            s.set_meta("inner", "2")
        assert "COMMIT" not in seen, "the inner scope committed nothing"
        s.set_meta("after", "3")
    assert seen.count("COMMIT") == 1
    assert (s.get_meta("outer"), s.get_meta("inner"), s.get_meta("after")) \
        == ("1", "2", "3")


def test_a_failure_inside_a_nested_scope_rolls_the_outer_one_back(s):
    with pytest.raises(ValueError):
        with s.transaction():
            s.set_meta("outer", "1")
            with s.transaction():
                s.set_meta("inner", "2")
                raise ValueError("deep failure")
    assert s.get_meta("outer") is None and s.get_meta("inner") is None


def test_swallowing_a_failure_inside_a_scope_aborts_rather_than_commits(s):
    """The deliberate refusal.

    A body that catches its own failure and returns normally is asking to
    commit a Commissioner action that partly did not happen. Rolling back
    silently would leave the caller believing it committed; committing
    would leave half an action. It raises.

    The poisoning covers failures that pass through a Storage mutating
    method, which is every write a route makes. Raw SQL executed on the
    connection and swallowed is outside it -- and no route does that any
    more, since the one that did (`_stale_sections`) is gone.
    """
    seen = _commits(s)
    with pytest.raises(TransactionAborted):
        with s.transaction():
            s.set_meta("kept?", "no")
            try:
                s.set_meta("bad", {"not": "a value sqlite can bind"})
            except Exception:
                pass          # exactly the mistake this catches
    assert s.get_meta("kept?") is None
    assert "ROLLBACK" in seen and "COMMIT" not in seen


def test_the_scope_is_usable_again_after_an_abort(s):
    """A failed transaction poisons itself, not the Storage."""
    with pytest.raises(ValueError):
        with s.transaction():
            s.set_meta("doomed", "1")
            raise ValueError("no")
    with s.transaction():
        s.set_meta("fine", "2")
    assert s.get_meta("fine") == "2" and s.get_meta("doomed") is None


# ----------------------------------------------------------------- reads

def test_reads_inside_a_write_scope_see_the_uncommitted_writes(s):
    """Reads go straight to the connection and never take a cursor scope,
    so they see the transaction they are inside."""
    with s.transaction():
        s.set_meta("k", "v")
        assert s.get_meta("k") == "v"
    assert s.get_meta("k") == "v"


def test_a_read_only_scope_commits_nothing_of_consequence(s):
    seen = _commits(s)
    with s.transaction():
        s.get_meta("nothing")
    assert seen.count("ROLLBACK") == 0


def test_an_early_return_from_inside_a_scope_still_commits(s):
    """A route that returns early has finished its action; the scope ends
    with the function and commits what it did."""
    def route():
        with s.transaction():
            s.set_meta("done", "1")
            return "early"

    assert route() == "early"
    assert s.get_meta("done") == "1"
