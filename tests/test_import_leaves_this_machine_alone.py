"""The import writes to the cloud and to nothing else.

WHY THIS IS THE ROLLBACK PROOF

Rolling back a cutover is meant to be one setting: unset
`LEAGUEPAGE_PROSE_BACKEND` and the Desk is reading its own machine
again, with everything exactly as it was. That is only true if the
import never touched the local store -- if it had rewritten a row, or
normalised a file, or bumped a timestamp, then rolling back would return
to a slightly different place than the one that was left, and the
difference would be invisible until it mattered.

So this watches every SQLite statement and every write under the
editorial tree while the import runs, and requires the count to be zero.
Not "no important writes". Zero.

The dry run half needs a real database to read, so it takes the same
opt-in every other live test takes. The static half runs everywhere.
"""
from __future__ import annotations

import importlib.util
import pathlib
import re
import sqlite3
import sys

import os

import pytest

WRITE_SQL = re.compile(r"^\s*(insert|update|delete|replace|create|drop|alter)\b",
                       re.I)
FS_WRITES = ("write_text", "write_bytes", "unlink", "mkdir", "rename",
             "touch", "chmod")
REPO = pathlib.Path(__file__).resolve().parent.parent


def _load():
    """Import the script by path; `scripts/` is not a package."""
    path = REPO / "scripts" / "import_editorial_state.py"
    spec = importlib.util.spec_from_file_location("import_editorial_state",
                                                  path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def watched_files(monkeypatch):
    """Every write attempted anywhere under a path, by name."""
    files: list[str] = []
    for name in FS_WRITES:
        real = getattr(pathlib.Path, name)

        def wrapper(self, *a, _real=real, _name=name, **kw):
            files.append(f"{_name}:{self}")
            return _real(self, *a, **kw)

        monkeypatch.setattr(pathlib.Path, name, wrapper)
    return files


def watch_sql(conn) -> list[str]:
    """Arm a trace on an ALREADY OPEN connection.

    Deliberately not a patch on `sqlite3.connect`: opening a Storage runs
    its `CREATE TABLE IF NOT EXISTS` bootstrap, which is the constructor
    ensuring its own schema and not the import moving anything. Watching
    from after the open keeps the assertion about the import and still
    catches DDL, so an import that tried to ALTER something would fail
    this rather than hide in the noise.
    """
    seen: list[str] = []
    conn.set_trace_callback(
        lambda st: seen.append(st.strip()[:80]) if WRITE_SQL.match(st) else None)
    return seen


LIVE = os.environ.get("LEAGUEPAGE_TEST_DATABASE_URL")


@pytest.mark.skipif(not LIVE, reason="live opt-in not set")
def test_the_dry_run_writes_nothing_at_all(watched_files):
    """A dry run that touched anything would not be a dry run."""
    import psycopg

    from leaguepage.storage import Storage

    mod = _load()
    with Storage() as s, psycopg.connect(LIVE, connect_timeout=30,
                                         autocommit=True) as pg:
        sql = watch_sql(s._conn)
        watched_files.clear()
        plan, _notes = mod.plan(s, pg)
        mod.prose_matches(pg)
        mod.state_plan(s, pg)
    assert plan, "the plan was empty, so nothing was actually exercised"
    assert sql == [], f"the dry run wrote to SQLite: {sql}"
    editorial = [f for f in watched_files if "editorial" in f.lower()]
    assert editorial == [], f"the dry run wrote to the editorial tree: {editorial}"


def test_the_import_never_names_a_local_write():
    """A static read of the script, to go with the dynamic one.

    The dynamic test only covers the paths a dry run takes. This one
    covers the whole file: nothing in it may hand a mutating statement to
    the local database, whatever branch it is on.
    """
    text = (REPO / "scripts" / "import_editorial_state.py").read_text(
        encoding="utf-8")
    # The local handle is always `s._conn`; every statement given to it
    # must be a SELECT or a PRAGMA.
    for m in re.finditer(r"s\._conn\.execute\(\s*\n?\s*(f?)([\"'])(.*?)\2",
                         text, re.S):
        statement = m.group(3).lstrip()
        assert statement.upper().startswith(("SELECT", "PRAGMA")), (
            f"the import hands a non-read to the local database: {statement[:60]}")
    assert "s._conn.executemany" not in text, (
        "executemany on the local handle can only be a write")
