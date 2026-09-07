"""EditorialStore: one Commissioner action, one transaction owner.

Runs against BOTH backends, so the contract is the thing being tested
rather than either implementation. The Postgres half is opt-in
(`LEAGUEPAGE_TEST_DATABASE_URL`) and writes only to a scratch namespace
that is purged either side of every test.

The difference the tranche exists to create is asserted directly: on the
filesystem a fault between the prose write and its description leaves
prose with no description -- honest, and the best a filesystem can do --
while on Postgres it leaves NEITHER. `act.atomic` says which promise is
in force, and `test_only_one_backend_promises_atomic_actions` pins that
the promise is not made falsely.
"""
from __future__ import annotations

from contextlib import contextmanager

import pytest

import leaguepage.issue_builder as ib
import leaguepage.matchup_packet as mp
from leaguepage import editorial_store as es
from leaguepage import prose_store as ps
from leaguepage import settings

T_LEAGUE, T_SEASON, T_ISSUE = "__store__", "1900", "week-99"
ACTOR = "commish@example.com"


class Boom(RuntimeError):
    """Injected failure."""


@contextmanager
def fault_in(state, method: str):
    """Break one state method for the duration of one action.

    Deliberately NOT monkeypatch: undoing a monkeypatch undoes every patch
    the fixture made too, including the editorial tree, and a test that
    then reads the real tree is measuring nothing.
    """
    cls = type(state)
    original = getattr(cls, method)

    def boom(*a, **kw):
        raise Boom(method)

    setattr(cls, method, boom)
    try:
        yield
    finally:
        setattr(cls, method, original)


def _an_authorized_commissioner() -> str | None:
    """Whoever the live allowlist admits. Read with the owner connection,
    which is reading configuration -- not a claim about authorization."""
    import psycopg

    from leaguepage.prose_postgres import PostgresProseRepository

    with psycopg.connect(PostgresProseRepository().dsn(), connect_timeout=20) as c:
        row = c.execute("select email from app_commissioners "
                        "order by email limit 1").fetchone()
    return row[0] if row else None


def _purge_postgres() -> None:
    import psycopg

    from leaguepage.prose_postgres import PostgresProseRepository

    with psycopg.connect(PostgresProseRepository().dsn(), connect_timeout=20,
                         autocommit=True) as conn:
        for table in ("prose_revisions", "prose_provenance", "sections",
                      "issue_modules", "matchup_state",
                      "issue_revision_requests", "research_artifacts"):
            conn.execute(f"delete from {table} where league_slug = %s",
                         (T_LEAGUE,))


@pytest.fixture(params=[ps.FILESYSTEM, ps.POSTGRES])
def store(request, tmp_path, monkeypatch):
    """One contract, every reachable backend."""
    if request.param == ps.POSTGRES:
        dsn = settings.get(settings.DATABASE_URL)
        if not dsn:
            pytest.skip("DATABASE_URL is not configured; postgres not reachable")
        # Somebody the live policy actually admits. A scratch namespace
        # is about WHICH ROWS, not about who is acting: acting as a
        # stranger would test RLS rather than the store.
        actor = _an_authorized_commissioner()
        if not actor:
            pytest.skip("app_commissioners is empty; nothing is authorized")
        _purge_postgres()
        yield es.PostgresEditorialStore(), actor
        _purge_postgres()
        return
    monkeypatch.setattr(ib, "EDITORIAL_DIR", tmp_path / "editorial")
    monkeypatch.setattr(mp, "EDITORIAL_DIR", tmp_path / "editorial")
    ps.reset_cache()
    yield es.FilesystemEditorialStore(tmp_path / "t.sqlite3"), ACTOR


def _k(name="fades", kind=ps.SECTION):
    return ps.ProseKey(T_LEAGUE, T_SEASON, T_ISSUE, kind, name)


def _read(store, key):
    """What a later request would see: a fresh action, fresh connection."""
    st, actor = store
    with st.action(actor=actor) as act:
        rec = act.prose.get(key)
        return rec.text, rec.version, act.state.prose_state(key), \
            act.state.provenance(key)


# ------------------------------------------------------------ the promise

def test_only_one_backend_promises_atomic_actions(store):
    st, actor = store
    with st.action(actor=actor) as act:
        assert act.atomic == (st.backend == ps.POSTGRES)
        assert st.health()["atomic_actions"] == act.atomic


def test_an_action_needs_to_say_who_is_acting():
    """Postgres authorizes on the actor, so an anonymous action would
    either be refused by RLS or -- worse -- run as the owner."""
    with pytest.raises(ps.ProseError, match="identity"):
        # Not a URL: the actor check happens before the connection, so
        # this never has to look like a DSN -- and a DSN-shaped
        # literal in a tracked file is what the privacy audit scans for.
        with es.PostgresEditorialStore("never-connected").action(actor=""):
            pass


# --------------------------------------------------------------- writing

def test_a_save_writes_prose_and_everything_describing_it(store):
    st, actor = store
    key = _k()
    with st.action(actor=actor) as act:
        first = act.save_section(key, "One.\n", expected_version=None,
                                 state="commissioner-edited")
    text, version, state, _prov = _read(store, key)
    assert text == "One.\n" and version == first.version
    assert state == "commissioner-edited"


def test_a_save_records_provenance_in_the_same_action(store):
    st, actor = store
    key = _k()
    with st.action(actor=actor) as act:
        act.save_section(key, "His words.\n", expected_version=None,
                         provenance_row={"origin": es.COMMISSIONER,
                                         "method": "section-brief",
                                         "generated_sha": "",
                                         "event": "commissioner-save"})
    _text, _v, _state, prov = _read(store, key)
    assert prov and prov["origin"] == es.COMMISSIONER
    assert prov["method"] == "section-brief"


def test_a_stale_save_changes_nothing_at_all(store):
    st, actor = store
    key = _k()
    with st.action(actor=actor) as act:
        act.save_section(key, "Stored.\n", expected_version=None,
                         state="commissioner-edited",
                         provenance_row={"origin": es.COMMISSIONER,
                                         "generated_sha": "abc"})
    before = _read(store, key)

    with pytest.raises(ps.ProseConflict):
        with st.action(actor=actor) as act:
            act.save_section(key, "Stale writer.\n",
                             expected_version="pg1:999" if st.backend ==
                             ps.POSTGRES else "fs1:" + "0" * 16,
                             state="generated",
                             provenance_row={"origin": "ai",
                                             "generated_sha": "zzz"})
    assert _read(store, key) == before, (
        "a refused write leaves no revision, no state change and no "
        "provenance change")


def test_saving_the_same_bytes_twice_describes_nothing_again(store):
    st, actor = store
    key = _k()
    with st.action(actor=actor) as act:
        v = act.save_section(key, "Same.\n", expected_version=None).version
    with st.action(actor=actor) as act:
        again = act.save_section(key, "Same.\n", expected_version=v,
                                 state="generated")
        assert again.version == v
    assert _read(store, key)[2] != "generated", (
        "not an edit, so nothing describing it should have moved")


# ----------------------------------------------------------- the history

def test_restore_reads_the_revision_inside_the_transaction(store):
    st, actor = store
    key = _k()
    with st.action(actor=actor) as act:
        v1 = act.save_section(key, "One.\n", expected_version=None).version
    with st.action(actor=actor) as act:
        v2 = act.save_section(key, "Two.\n", expected_version=v1).version
    with st.action(actor=actor) as act:
        rows = act.prose.history(key)
        assert [r["prior_text"] for r in rows] == ["One.\n"]
        act.restore(key, rows[0]["id"], expected_version=v2)
    assert _read(store, key)[0] == "One.\n"


def test_restoring_an_unknown_revision_writes_nothing(store):
    st, actor = store
    key = _k()
    with st.action(actor=actor) as act:
        v = act.save_section(key, "Only.\n", expected_version=None).version
    with pytest.raises(ps.ProseError):
        with st.action(actor=actor) as act:
            act.restore(key, 987654321, expected_version=v)
    assert _read(store, key)[0] == "Only.\n"


# --------------------------------------------------------- the proposals

def test_accepting_a_proposal_writes_the_target_and_retires_the_proposal(store):
    st, actor = store
    target, proposal = _k(), _k("fades", ps.PROPOSAL)
    with st.action(actor=actor) as act:
        v = act.save_section(target, "His own words.\n",
                             expected_version=None).version
        act.prose.put(proposal, "A proposed rewrite.\n", expected_version=None)
    with st.action(actor=actor) as act:
        act.accept_proposal(target, proposal, "A proposed rewrite.\n",
                            expected_version=v)
    assert _read(store, target)[0] == "A proposed rewrite.\n"
    with st.action(actor=actor) as act:
        assert not act.prose.get(proposal).exists


def test_retiring_a_proposal_twice_is_success_not_an_error(store):
    """Idempotent by design: the outcome the caller asked for is the
    outcome, so a retry after a partial failure is safe."""
    st, actor = store
    target, proposal = _k(), _k("fades", ps.PROPOSAL)
    with st.action(actor=actor) as act:
        v = act.save_section(target, "Base.\n", expected_version=None).version
        act.prose.put(proposal, "Rewrite.\n", expected_version=None)
    with st.action(actor=actor) as act:
        act.accept_proposal(target, proposal, "Rewrite.\n",
                            expected_version=v)
    with st.action(actor=actor) as act:
        act.prose.delete(proposal)          # again
        assert not act.prose.get(proposal).exists


def test_accepting_a_proposal_that_moved_is_refused(store):
    st, actor = store
    target, proposal = _k(), _k("fades", ps.PROPOSAL)
    with st.action(actor=actor) as act:
        v = act.save_section(target, "Base.\n", expected_version=None).version
        pv = act.prose.put(proposal, "First draft.\n",
                           expected_version=None).version
    with st.action(actor=actor) as act:
        act.prose.put(proposal, "A second Claude run rewrote it.\n",
                      expected_version=pv)
    with pytest.raises(ps.ProseConflict):
        with st.action(actor=actor) as act:
            act.accept_proposal(target, proposal, "First draft.\n",
                                expected_version=v, proposal_version=pv)
    assert _read(store, target)[0] == "Base.\n", "the target is untouched"


# ---------------------------------------------------------- the approval

def test_approve_records_the_signature_it_observed(store):
    st, actor = store
    with st.action(actor=actor) as act:
        act.approve(T_LEAGUE, T_SEASON, T_ISSUE, "fades", "sig-of-the-text")
    with st.action(actor=actor) as act:
        row = act.state.module(T_LEAGUE, T_SEASON, T_ISSUE, "fades")
    assert row["approved"] and row["approved_sha"] == "sig-of-the-text"


def test_unapprove_clears_the_signature_rather_than_leaving_a_bare_true(store):
    st, actor = store
    with st.action(actor=actor) as act:
        act.approve(T_LEAGUE, T_SEASON, T_ISSUE, "fades", "sig")
    with st.action(actor=actor) as act:
        act.unapprove(T_LEAGUE, T_SEASON, T_ISSUE, "fades")
    with st.action(actor=actor) as act:
        row = act.state.module(T_LEAGUE, T_SEASON, T_ISSUE, "fades")
    assert not row["approved"] and not row["approved_sha"]


def test_ctp_approval_records_what_each_preview_said(store):
    st, actor = store
    with st.action(actor=actor) as act:
        act.approve(T_LEAGUE, T_SEASON, T_ISSUE, "ctp", "composite-sig",
                    covered={(99, "a-vs-b"): "sha-a", (99, "c-vs-d"): "sha-c"})
    with st.action(actor=actor) as act:
        assert act.state.matchup(T_LEAGUE, T_SEASON, 99,
                                 "a-vs-b")["covered_sha"] == "sha-a"
        assert act.state.matchup(T_LEAGUE, T_SEASON, 99,
                                 "c-vs-d")["covered_sha"] == "sha-c"
        assert act.state.module(T_LEAGUE, T_SEASON, T_ISSUE,
                                "ctp")["approved_sha"] == "composite-sig"


# ------------------------------------------------------- fault injection

@pytest.mark.parametrize("seam", ["provenance", "state", "matchup"])
def test_a_fault_between_the_prose_and_its_description(store, seam):
    """The difference between the two backends, stated as a test.

    Postgres: neither half survives. Filesystem: the prose survives with
    no description, which is honest but is not atomicity -- and is exactly
    why Tranche 5B exists.
    """
    st, actor = store
    # The matchup seam is only reached by a preview save, so that case
    # uses a preview key. Injecting it into a section save would break
    # nothing and the test would pass for the wrong reason.
    matchup = seam == "matchup"
    key = _k("a-vs-b", ps.MATCHUP) if matchup else _k()
    week = 99 if matchup else None
    with st.action(actor=actor) as act:
        v = act.save_section(key, "Before.\n", expected_version=None,
                             state="commissioner-edited", week=week).version
        if matchup:
            act.state.set_matchup(T_LEAGUE, T_SEASON, 99, "a-vs-b",
                                  status="approved")

    target = {"provenance": "set_provenance", "state": "set_prose_state",
              "matchup": "matchup"}[seam]

    with pytest.raises(Boom):
        with st.action(actor=actor) as act:
            with fault_in(act.state, target):
                act.save_section(key, "After.\n", expected_version=v,
                                 state="commissioner-edited", week=week,
                                 provenance_row={"origin": "ai",
                                                 "generated_sha": "x"})

    text, _v, state, prov = _read(store, key)
    if st.backend == ps.POSTGRES:
        assert text == "Before.\n", "the whole action rolled back"
        assert state == "commissioner-edited"
        assert not (prov and prov.get("origin") == "ai")
    else:
        assert text == "After.\n", "the filesystem write cannot be rolled back"
        assert not (prov and prov.get("origin") == "ai"), (
            "but nothing describing it was committed either")


def test_a_fault_while_retiring_a_proposal(store):
    """The route 5A could not make atomic. On Postgres, a failure after the
    target write must leave the target unchanged and the proposal offered;
    on the filesystem the target moves and the proposal stays, which is the
    state 5A documented and could only make recoverable."""
    st, actor = store
    target, proposal = _k(), _k("fades", ps.PROPOSAL)
    with st.action(actor=actor) as act:
        v = act.save_section(target, "His own words.\n",
                             expected_version=None).version
        act.prose.put(proposal, "A proposed rewrite.\n", expected_version=None)

    with pytest.raises(Boom):
        with st.action(actor=actor) as act:
            with fault_in(act.state, "resolve_rewrite_requests"):
                act.accept_proposal(target, proposal, "A proposed rewrite.\n",
                                    expected_version=v)

    text = _read(store, target)[0]
    with st.action(actor=actor) as act:
        proposal_survives = act.prose.get(proposal).exists
    if st.backend == ps.POSTGRES:
        assert text == "His own words.\n" and proposal_survives, (
            "neither half happened")
    else:
        assert text == "A proposed rewrite.\n", (
            "the filesystem accepted; 5A's recovery path covers this")


# ------------------------------------------------------------ concurrency

def test_two_writers_from_the_same_version_and_only_one_wins(store):
    st, actor = store
    key = _k()
    with st.action(actor=actor) as act:
        v = act.save_section(key, "Base.\n", expected_version=None).version

    with st.action(actor=actor) as act:
        act.save_section(key, "A got there first.\n", expected_version=v)
    with pytest.raises(ps.ProseConflict):
        with st.action(actor=actor) as act:
            act.save_section(key, "B was reading the old one.\n",
                             expected_version=v)
    assert _read(store, key)[0] == "A got there first.\n"


def test_approve_then_save_leaves_the_approval_describing_the_old_text(store):
    """One valid serial ordering. The approval is not wrong -- it is a
    true statement about a text that is no longer stored, which is exactly
    what the signature comparison is for."""
    st, actor = store
    key = _k()
    with st.action(actor=actor) as act:
        v = act.save_section(key, "Approved text.\n",
                             expected_version=None).version
        act.approve(T_LEAGUE, T_SEASON, T_ISSUE, "fades", "sig-of-approved")
    with st.action(actor=actor) as act:
        act.save_section(key, "Edited after approval.\n", expected_version=v)
    with st.action(actor=actor) as act:
        row = act.state.module(T_LEAGUE, T_SEASON, T_ISSUE, "fades")
    assert row["approved_sha"] == "sig-of-approved", (
        "the approval still records what it covered; the comparison is "
        "what makes it stop counting")
