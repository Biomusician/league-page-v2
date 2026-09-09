"""Site -> About, against whichever backend is authoritative.

About was the last authoring surface writing authoritative state to this
machine. It now goes through `EditorialState` like every other one, so the
save is inside the store's transaction and, on Postgres, runs as the
signed-in Commissioner with RLS applying to it.

The contract is deliberately narrow, and the narrowness is the thing under
test as much as the behaviour:

  * one document per slug, replaced in place. No revision row, no history
    table, no draft, no workflow.
  * the shipped default when nothing has been written -- on both backends,
    because "no override yet" is a real state and not an error.
  * `updated_by` is recorded in Postgres and dropped on the filesystem,
    where git already answers the question.

There is no optimistic-concurrency contract on this route, and this file
does not invent one. `test_two_saves_from_the_same_view_last_one_wins`
pins the behaviour that actually exists, and proves the thing that would
matter if it were wrong: a save here cannot touch any coupled state,
because there is none to touch.

The live half needs LEAGUEPAGE_TEST_DATABASE_URL and migration 0007.
"""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

import leaguepage.issue_builder as ib
from leaguepage import editorial_store, prose_store, site_documents
from leaguepage.desk import create_app
from leaguepage.storage import Storage

LIVE = os.environ.get("LEAGUEPAGE_TEST_DATABASE_URL")

# A slug the product will never author, for everything that can use one.
SCRATCH = "__scratch-doc__"


# --------------------------------------------------------------- filesystem

@pytest.fixture
def fs(tmp_path, monkeypatch):
    """A filesystem-backed Desk whose editorial tree is under tmp."""
    ed = tmp_path / "editorial"
    monkeypatch.setattr(ib, "EDITORIAL_DIR", ed)
    monkeypatch.setenv(prose_store.BACKEND_SETTING, prose_store.FILESYSTEM)
    db = tmp_path / "t.sqlite3"
    with Storage(db):
        pass
    return TestClient(create_app(db_path=db)), ed


def test_the_default_is_served_when_nothing_has_been_written(fs):
    _client, ed = fs
    assert not (ed / "site" / "about.md").exists()
    assert site_documents.read() == site_documents.DEFAULT_ABOUT


def test_a_save_becomes_the_answer_and_the_default_stops_being_read(fs):
    client, ed = fs
    body = "# About\n\nWritten by hand.\n"
    r = client.post("/commissioner/site/about",
                    data={"text": body, "action": "save"},
                    follow_redirects=True)
    assert r.status_code == 200
    assert (ed / "site" / "about.md").read_text(encoding="utf-8") == body
    assert site_documents.read() == body


def test_the_override_survives_a_restart(fs, tmp_path):
    """A new app, a new store, a new process's worth of state."""
    client, ed = fs
    client.post("/commissioner/site/about",
                data={"text": "# Kept\n", "action": "save"},
                follow_redirects=True)
    fresh = TestClient(create_app(db_path=tmp_path / "t.sqlite3"))
    page = fresh.get("/commissioner/site/about")
    assert "# Kept" in page.text


def test_an_update_replaces_rather_than_accumulating(fs):
    client, ed = fs
    for body in ("# One\n", "# Two\n", "# Three\n"):
        client.post("/commissioner/site/about",
                    data={"text": body, "action": "save"},
                    follow_redirects=True)
    assert site_documents.read() == "# Three\n"
    assert list((ed / "site").iterdir()) == [ed / "site" / "about.md"], \
        "one document, one file: no revisions, no drafts"


def test_preview_writes_nothing(fs):
    """The preview route renders Markdown and must not persist anything --
    the previous route audit believed otherwise."""
    client, ed = fs
    r = client.post("/commissioner/site/about/preview",
                    json={"text": "# Not saved\n"})
    assert r.status_code == 200 and r.json()["ok"]
    assert not (ed / "site").exists()


def test_two_saves_from_the_same_view_last_one_wins(fs):
    """The honest description of a route with no version contract.

    It is safe here for a reason worth stating: an About save writes one
    row and nothing else. There is no coupled state for a stale request to
    half-mutate, which is the property that makes the missing version check
    a limitation rather than a bug.
    """
    client, _ed = fs
    client.get("/commissioner/site/about")           # two editors open it
    client.post("/commissioner/site/about",
                data={"text": "# A\n", "action": "save"},
                follow_redirects=True)
    client.post("/commissioner/site/about",
                data={"text": "# B\n", "action": "save"},
                follow_redirects=True)
    assert site_documents.read() == "# B\n"


def test_the_editor_names_a_path_on_the_filesystem(fs):
    client, _ed = fs
    page = client.get("/commissioner/site/about").text
    assert "site/about.md" in page
    assert "DATABASE_URL" not in page


# ----------------------------------------------------------------- postgres

live = pytest.mark.skipif(not LIVE, reason="live opt-in not set")


def _applied() -> bool:
    import psycopg

    with psycopg.connect(LIVE, connect_timeout=30, autocommit=True) as c:
        return c.execute(
            "select to_regclass('public.site_documents') is not null"
        ).fetchone()[0]


needs_0007 = pytest.mark.skipif(
    LIVE is not None and not _applied(),
    reason="migration 0007 has not been applied to this database yet")


def _commissioner() -> str:
    import psycopg

    with psycopg.connect(LIVE, connect_timeout=30, autocommit=True) as c:
        row = c.execute("select email from app_commissioners "
                        "order by email limit 1").fetchone()
    assert row, "the live database has no allowlisted Commissioner"
    return row[0]


def _selecting_postgres(real_get):
    def get(name, default=None):
        if name == prose_store.BACKEND_SETTING:
            return prose_store.POSTGRES
        if name == "DATABASE_URL":
            return LIVE
        return real_get(name, default)
    return get


@pytest.fixture
def pg_about(monkeypatch, tmp_path):
    """Postgres selected IN THIS PROCESS ONLY, and the real `about` row put
    back exactly as it was found -- including "there wasn't one".

    Same shape as `test_routes_on_postgres.py`: the backend is chosen by
    patching `settings.get`, nothing is written to `.env`, and the
    signed-in identity is supplied directly because authentication itself
    is covered elsewhere and what is under test here is what the store does
    with an actor.
    """
    import psycopg

    from leaguepage import auth, settings

    monkeypatch.setattr(settings, "get", _selecting_postgres(settings.get))
    monkeypatch.setattr(auth, "actor_of", lambda request: _commissioner())
    prose_store.reset_cache()
    monkeypatch.setattr(ib, "EDITORIAL_DIR", tmp_path / "editorial")

    with psycopg.connect(LIVE, connect_timeout=30, autocommit=True) as c:
        before = c.execute(
            "select body, updated_by from site_documents where slug=%s",
            (site_documents.ABOUT,)).fetchone()
        c.execute("delete from site_documents where slug in (%s, %s)",
                  (site_documents.ABOUT, SCRATCH))
    try:
        yield tmp_path / "editorial"
    finally:
        prose_store.reset_cache()
        with psycopg.connect(LIVE, connect_timeout=30, autocommit=True) as c:
            c.execute("delete from site_documents where slug in (%s, %s)",
                      (site_documents.ABOUT, SCRATCH))
            if before is not None:
                c.execute("insert into site_documents (slug, body, updated_by) "
                          "values (%s, %s, %s)",
                          (site_documents.ABOUT, before[0], before[1]))


@live
@needs_0007
def test_no_row_means_the_shipped_default(pg_about):
    assert site_documents.read() == site_documents.DEFAULT_ABOUT


@live
@needs_0007
def test_a_row_is_the_answer_and_the_commissioner_is_recorded(pg_about):
    import psycopg

    email = _commissioner()
    with editorial_store.store().action(actor=email) as act:
        act.state.set_site_document(site_documents.ABOUT, "# Cloud\n", email)

    assert site_documents.read() == "# Cloud\n"
    with psycopg.connect(LIVE, connect_timeout=30, autocommit=True) as c:
        body, by, at = c.execute(
            "select body, updated_by, updated_at from site_documents "
            "where slug=%s", (site_documents.ABOUT,)).fetchone()
    assert body == "# Cloud\n"
    assert by == email
    assert at is not None


@live
@needs_0007
def test_an_update_moves_updated_at_and_keeps_one_row(pg_about):
    import time

    import psycopg

    email = _commissioner()
    with editorial_store.store().action(actor=email) as act:
        act.state.set_site_document(site_documents.ABOUT, "# First\n", email)
    with psycopg.connect(LIVE, connect_timeout=30, autocommit=True) as c:
        first = c.execute("select updated_at from site_documents where slug=%s",
                          (site_documents.ABOUT,)).fetchone()[0]
    time.sleep(0.05)
    with editorial_store.store().action(actor=email) as act:
        act.state.set_site_document(site_documents.ABOUT, "# Second\n", email)
    with psycopg.connect(LIVE, connect_timeout=30, autocommit=True) as c:
        rows = c.execute(
            "select body, updated_at from site_documents where slug=%s",
            (site_documents.ABOUT,)).fetchall()
    assert len(rows) == 1, "an update must replace, not accumulate"
    assert rows[0][0] == "# Second\n"
    assert rows[0][1] > first


@live
@needs_0007
def test_the_route_saves_to_the_cloud_and_writes_no_file(pg_about, tmp_path):
    """The claim that matters for a hosted Desk: authoritative state left
    the filesystem, and did not leave a copy behind."""
    import psycopg

    db = tmp_path / "t.sqlite3"
    with Storage(db):
        pass
    client = TestClient(create_app(db_path=db))
    r = client.post("/commissioner/site/about",
                    data={"text": "# Hosted\n", "action": "save"},
                    follow_redirects=True)
    assert r.status_code == 200

    with psycopg.connect(LIVE, connect_timeout=30, autocommit=True) as c:
        assert c.execute("select body from site_documents where slug=%s",
                         (site_documents.ABOUT,)).fetchone()[0] == "# Hosted\n"
    assert not (pg_about / "site").exists(), \
        "postgres mode wrote editorial/site/about.md as well"


@live
@needs_0007
def test_the_editor_names_the_table_not_a_dsn(pg_about, tmp_path):
    db = tmp_path / "t.sqlite3"
    with Storage(db):
        pass
    page = TestClient(create_app(db_path=db)).get(
        "/commissioner/site/about").text
    assert "site_documents" in page
    # Against the project's own definition of a secret rather than a list
    # of literals: a hand-written list drifts, and writing one here would
    # put connection-string shapes in a tracked file for no reason.
    from leaguepage.privacy import PRIVATE_PATTERNS

    for pat, label in PRIVATE_PATTERNS:
        if label in ("email address", "internal field name", "absolute path",
                     "private repo path", "authoring artifact"):
            continue
        assert not pat.search(page), f"the About editor leaked a {label}"
    for leak in ("pooler.supabase.com", "password", "DATABASE_URL"):
        assert leak not in page, f"the About editor named {leak!r}"


@live
@needs_0007
def test_a_save_without_an_identity_is_refused(pg_about):
    """RLS authorizes on the actor, so an anonymous action would either be
    refused or -- worse -- run as the owner and skip the policy."""
    with pytest.raises(prose_store.ProseError):
        with editorial_store.store().action(actor="") as act:
            act.state.set_site_document(site_documents.ABOUT, "# nope\n")


@live
@needs_0007
def test_a_stranger_cannot_write_the_about_page(pg_about):
    """Signed in is not allowed. The database refuses, not the route."""
    import psycopg

    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with editorial_store.store().action(
                actor="not-the-commissioner@example.invalid") as act:
            act.state.set_site_document(site_documents.ABOUT, "# theirs\n")

    with psycopg.connect(LIVE, connect_timeout=30, autocommit=True) as c:
        assert c.execute("select count(*) from site_documents where slug=%s",
                         (site_documents.ABOUT,)).fetchone()[0] == 0
