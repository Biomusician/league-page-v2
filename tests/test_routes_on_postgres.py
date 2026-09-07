"""The real routes, driven over HTTP, against the real database.

`test_editorial_store.py` proves the store keeps its contract when it is
called directly. This proves the routes call it -- which is a different
claim, and the one Tranche 5C section 6 insists on separately: a route
can reach EditorialStore and still leave a write outside it, and the
difference only shows when something fails halfway.

So the application is built with the Postgres backend selected IN THIS
PROCESS ONLY. Nothing is written to `.env`, no setting outside this test
changes, and every row lands in a scratch namespace that is deleted
either side. Selecting a backend for the length of a test is not a
cutover, and this file is careful to stay on the right side of that.

Opt in with LEAGUEPAGE_TEST_DATABASE_URL, exactly like the other live
tests.

WHAT IS ACTUALLY BEING COMPARED

On the filesystem a save is two commits: the words, then everything that
describes them. A failure between them leaves prose nothing describes,
which is honest but is a gap. On Postgres the same route call is one
transaction, so the gap cannot exist. These tests inject a fault at the
metadata seam and check that the WORDS did not survive either -- because
that, and not the happy path, is the whole difference the tranche exists
to create.
"""
from __future__ import annotations

import json
import os
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

import leaguepage.config as cfg
import leaguepage.issue_builder as ib
import leaguepage.matchup_packet as mp
from leaguepage import auth, editorial_state as est, prose_store
from leaguepage.config import get_league
from leaguepage.desk import create_app
from leaguepage.storage import Storage

from fixtures import populate_league, populate_matchups

LIVE = os.environ.get("LEAGUEPAGE_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not LIVE, reason="live opt-in not set")

# A namespace no league will ever have. Rows here are deleted either side
# of every test, and nothing in the real editorial data shares it.
LG_SLUG, SEASON, ISSUE = "surfeit", "1900", "week-99"
SCRATCH = "__routes__"
EDIT = f"/commissioner/{LG_SLUG}/{SEASON}/issue/{ISSUE}/edit"
LG = get_league(LG_SLUG)

SCRATCH_TABLES = ("prose_revisions", "prose_provenance", "sections",
                  "issue_modules", "matchup_state", "issue_revision_requests",
                  "research_artifacts", "issues")


class Boom(RuntimeError):
    """A seam failing for reasons the route cannot know about."""


@contextmanager
def fault_in(cls, method: str):
    original = getattr(cls, method)

    def boom(*a, **kw):
        raise Boom(method)

    setattr(cls, method, boom)
    try:
        yield
    finally:
        setattr(cls, method, original)


def _purge() -> None:
    import psycopg

    with psycopg.connect(LIVE, connect_timeout=30, autocommit=True) as c:
        for table in SCRATCH_TABLES:
            cols = {r[0] for r in c.execute(
                "select column_name from information_schema.columns "
                "where table_schema='public' and table_name=%s",
                (table,)).fetchall()}
            if "season" in cols:
                c.execute(f"delete from {table} where season = %s", (SEASON,))


def _commissioner() -> str | None:
    import psycopg

    with psycopg.connect(LIVE, connect_timeout=30, autocommit=True) as c:
        row = c.execute("select email from app_commissioners "
                        "order by email limit 1").fetchone()
    return row[0] if row else None


def _cloud(sql: str, args: tuple):
    import psycopg

    with psycopg.connect(LIVE, connect_timeout=30, autocommit=True) as c:
        return c.execute(sql, args).fetchone()


@pytest.fixture
def desk(tmp_path, monkeypatch):
    """A Desk whose authored state is Postgres and whose analytics are not.

    Sleeper data stays in SQLite because that is what it is: a synced
    cache this test has to populate to make a week exist. Everything the
    Commissioner authors goes to the cloud, which is the half under test.
    """
    actor = _commissioner()
    if not actor:
        pytest.skip("app_commissioners is empty; nothing is authorized")

    from leaguepage import settings

    monkeypatch.setattr(settings, "get", _selecting_postgres(settings.get))
    prose_store.reset_cache()
    # The route reads the signed-in identity from the session. Auth itself
    # is covered by tests/test_auth.py; what is under test here is what
    # the store does with an actor, so it is supplied directly.
    monkeypatch.setattr(auth, "actor_of", lambda request: actor)

    ed = tmp_path / "editorial"
    monkeypatch.setattr(ib, "EDITORIAL_DIR", ed)
    monkeypatch.setattr(mp, "EDITORIAL_DIR", ed)
    monkeypatch.setattr(cfg, "PUBLISHED_DIR", tmp_path / "published")
    monkeypatch.setattr(mp, "load_managers", lambda: {})
    monkeypatch.setattr(mp, "load_coalitions",
                        lambda: {"identities": {}, "coalitions": [],
                                 "relationships": []})
    db = tmp_path / "analytics.sqlite3"
    with Storage(db) as s:
        populate_league(s, LG, teams=10, rounds=3, picks="complete",
                        season=SEASON)
        populate_matchups(s, LG, week=99, teams=10,
                          scores={r: 90.0 + r for r in range(1, 11)})
        s.set_meta("current_week", "99")
    _purge()
    yield TestClient(create_app(db_path=db)), actor
    _purge()
    prose_store.reset_cache()


def _selecting_postgres(real_get):
    def get(name, default=None):
        if name == prose_store.BACKEND_SETTING:
            return prose_store.POSTGRES
        if name == "DATABASE_URL":
            return LIVE
        return real_get(name, default)
    return get


def _save(client, text, expected=..., section="lowdown"):
    """Save the way the Desk's own client does.

    A save that names no version is claiming the section does not exist
    yet, which is the route's documented refusal to write blind. So the
    default here is to read what is stored and write against it, and a
    test that means to be stale says so.
    """
    if expected is ...:
        expected = client.get(f"{EDIT}/section-state",
                              params={"section": section}).json().get("version")
    body = {"section": section, "text": text}
    if expected is not None:
        body["expected_version"] = expected
    return client.post(f"{EDIT}/save", json=body)


def _stored():
    return _cloud("select content, state from sections where league_slug=%s "
                  "and season=%s and issue_key=%s and kind='section' "
                  "and section='lowdown'", (LG_SLUG, SEASON, ISSUE))


def _prov():
    return _cloud("select origin, event from prose_provenance where "
                  "league_slug=%s and season=%s and issue_key=%s and "
                  "section='lowdown'", (LG_SLUG, SEASON, ISSUE))


# ------------------------------------------------------------ happy path

def test_the_backend_under_test_really_is_postgres(desk):
    """The check that stops every test below from being about SQLite."""
    client, _actor = desk
    assert prose_store.backend_name() == prose_store.POSTGRES
    r = client.get("/health")
    assert r.status_code == 200


def test_a_save_lands_in_the_cloud_with_everything_describing_it(desk):
    client, _actor = desk
    assert _save(client, "First words.\n").status_code == 200
    row = _stored()
    assert row and row[0] == "First words.\n"
    assert row[1] == "commissioner-edited", (
        "sections.state is the cloud's prose state and the route must set it")


def test_the_words_and_their_description_are_one_transaction(desk):
    """The difference from the filesystem, stated as an experiment.

    A save into an empty section settles origin as the Commissioner's and
    sets a prose state. Break the state write and NEITHER survives -- not
    the provenance, and not the words. On the filesystem the words would
    be there with nothing describing them.
    """
    client, _actor = desk
    with fault_in(est.PostgresEditorialState, "set_prose_state"):
        with pytest.raises(Boom):
            _save(client, "Words that must not survive.\n")
    assert _stored() is None, (
        "the prose committed even though the action failed; that is the "
        "filesystem's behaviour and Postgres is supposed to be better")
    assert _prov() is None


def test_a_stale_save_changes_nothing(desk):
    client, _actor = desk
    assert _save(client, "One.\n").status_code == 200
    version = client.get(f"{EDIT}/section-state",
                         params={"section": "lowdown"}).json()["version"]
    assert _save(client, "Two.\n", expected=version).status_code == 200
    stale = _save(client, "Three.\n", expected=version)
    assert stale.status_code == 409, stale.text
    assert _stored()[0] == "Two.\n"


def test_two_writers_from_the_same_version_and_only_one_wins(desk):
    client, _actor = desk
    assert _save(client, "Start.\n").status_code == 200
    version = client.get(f"{EDIT}/section-state",
                         params={"section": "lowdown"}).json()["version"]
    first = _save(client, "Laptop.\n", expected=version)
    second = _save(client, "Phone.\n", expected=version)
    assert first.status_code == 200 and second.status_code == 409
    assert _stored()[0] == "Laptop.\n"


def test_approval_records_a_signature_over_what_it_approved(desk):
    client, _actor = desk
    assert _save(client, "Approvable prose.\n").status_code == 200
    r = client.post(f"{EDIT}/approve",
                    json={"section": "lowdown", "action": "approve"})
    assert r.status_code == 200, r.text
    row = _cloud("select approved, approved_sha from issue_modules where "
                 "league_slug=%s and season=%s and issue_key=%s and "
                 "module_key='lowdown'", (LG_SLUG, SEASON, ISSUE))
    assert row and row[0] and row[1], "approved with no signature"

    # And the signature retires itself when the words move, with nothing
    # written to make that happen.
    signed = row[1]
    assert _save(client, "Rewritten by hand.\n").status_code == 200
    after = _cloud("select approved_sha from issue_modules where "
                   "league_slug=%s and season=%s and issue_key=%s and "
                   "module_key='lowdown'", (LG_SLUG, SEASON, ISSUE))
    assert after[0] == signed, (
        "the approval should still record what it covered; it is the "
        "comparison that retires it, not a write")


def test_a_failed_approval_leaves_no_half_written_coverage(desk):
    """CTP's approval is one click and up to seven writes.

    On SQLite those were seven commits. Here the module row and every
    preview's coverage share a transaction, so a failure part way leaves
    none of it -- including the module row that was written first.
    """
    client, _actor = desk
    assert _save(client, "Approvable prose.\n").status_code == 200
    with fault_in(est.PostgresEditorialState, "set_module"):
        with pytest.raises(Boom):
            client.post(f"{EDIT}/approve",
                        json={"section": "lowdown", "action": "approve"})
    row = _cloud("select approved from issue_modules where league_slug=%s "
                 "and season=%s and issue_key=%s and module_key='lowdown'",
                 (LG_SLUG, SEASON, ISSUE))
    assert row is None or not row[0]


def test_a_take_and_the_decision_beside_it_are_one_action(desk):
    """`matchup_angle`'s select branch writes two tables in one click."""
    client, _actor = desk
    slug = "team-1-vs-team-2"
    url = f"/commissioner/{LG_SLUG}/{SEASON}/week/99/matchups/{slug}/angle"
    with fault_in(est.PostgresEditorialState, "set_story_decision"):
        with pytest.raises(Boom):
            client.post(url, data={"action": "select", "angle_id": "a1",
                                   "note": "because"})
    row = _cloud("select status from matchup_state where league_slug=%s and "
                 "season=%s and week=99 and matchup_slug=%s",
                 (LG_SLUG, SEASON, slug))
    assert row is None, (
        "the angle was selected even though recording the decision failed")


def test_research_is_read_from_the_cloud_not_the_disk(desk):
    """Reset-to-generated on a Desk with no issue directory.

    The rough draft lives in `research_artifacts` here. Before the
    research port this route stat'd a path that does not exist on a
    hosted Desk and answered 404 for every section, forever.
    """
    import psycopg

    client, _actor = desk
    assert _save(client, "His own words.\n").status_code == 200
    draft = "ROUGH DRAFT - COMMISSIONER EDIT REQUIRED\n\nGenerated words.\n"
    with psycopg.connect(LIVE, connect_timeout=30, autocommit=True) as c:
        c.execute("insert into research_artifacts (league_slug, season, "
                  "issue_key, scope, name, body) values (%s,%s,%s,%s,%s,%s)",
                  (LG_SLUG, SEASON, ISSUE, "lowdown", "rough-lowdown.md",
                   draft))
    r = client.post(f"{EDIT}/reset-generated",
                    json={"section": "lowdown", "confirm": "yes"})
    assert r.status_code == 200, r.text
    row = _stored()
    assert row and row[0] == draft
    assert row[1] == "generated"
    assert _prov()[0] == "ai", "a marked draft is AI in origin"


def test_nothing_outside_the_scratch_namespace_was_touched(desk):
    """The guard on the guard.

    Every test here writes into season 1900, which no league has. If a
    route ever wrote outside it, this would be the only thing that
    noticed.
    """
    client, _actor = desk
    assert _save(client, "Scratch only.\n").status_code == 200
    row = _cloud("select count(*) from sections where season = %s "
                 "and league_slug = %s", (SEASON, LG_SLUG))
    assert row[0] >= 1
    other = _cloud("select count(*) from sections where season <> %s "
                   "and issue_key = %s", (SEASON, ISSUE))
    assert other[0] == 0, "a scratch issue key leaked into a real season"
