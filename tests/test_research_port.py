"""Research: a file on this machine, a row in the cloud.

The rough draft is the one research artifact the application READS on a
path that decides what gets written -- what Reset puts back into the box,
and whether a save records that AI help was present. Everything else in
the issue directory is read by screens that only display it.

The filesystem adapter turns (scope, name) into path segments, so it
checks them. The Postgres adapter stores them as columns and does not
need to, but the port has one contract and both sides keep it.
"""
from __future__ import annotations

import pytest

from leaguepage.editorial_state import SqliteEditorialState
from leaguepage.storage import Storage

ARGS = ("surfeit", "2026", "week-01", "lowdown", "rough-lowdown.md")


@pytest.fixture
def state(tmp_path):
    with Storage(":memory:") as s:
        yield SqliteEditorialState(s, base_dir=tmp_path), tmp_path


def test_nothing_there_reads_as_nothing(state):
    st, _ = state
    assert st.research(*ARGS) is None


def test_it_lands_where_a_claude_code_session_would_write_it(state):
    """The point of the filesystem adapter: the same file as before.

    A session writes `rough-lowdown.md` into the issue directory, and a
    port that stored it somewhere else would leave every existing
    workflow writing to a place the Desk no longer reads.
    """
    st, base = state
    st.set_research(*ARGS, "A rough draft.\n")
    assert st.research(*ARGS) == "A rough draft.\n"
    assert (base / "2026" / "surfeit" / "week-01" / "lowdown"
            / "rough-lowdown.md").read_text(encoding="utf-8") == "A rough draft.\n"


def test_saving_over_it_replaces_it(state):
    st, _ = state
    st.set_research(*ARGS, "First.\n")
    st.set_research(*ARGS, "Second.\n")
    assert st.research(*ARGS) == "Second.\n"


@pytest.mark.parametrize("scope,name", [
    ("..", "x.md"),
    ("lowdown", "../x.md"),
    ("lowdown", "..\\x.md"),
    (".", "x.md"),
    ("lowdown", ".env"),
])
def test_a_segment_that_could_escape_the_issue_is_refused(state, scope, name):
    """Both segments become path components, so both are checked.

    Not a hypothetical: `scope` and `name` reach this from a route, and
    the whole point of the port is that the same call runs against a
    store where they are only columns.
    """
    st, _ = state
    with pytest.raises(ValueError):
        st.research("surfeit", "2026", "week-01", scope, name)
    with pytest.raises(ValueError):
        st.set_research("surfeit", "2026", "week-01", scope, name, "x")


def test_a_missing_directory_is_not_an_error(state):
    """An issue nobody has run a session for has no directory at all."""
    st, _ = state
    assert st.research("surfeit", "2026", "week-42", "lowdown",
                       "rough-lowdown.md") is None
