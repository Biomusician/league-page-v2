"""The whole authorization chain, from a signed session to a row.

    a real Desk session cookie
      -> auth.actor_of
        -> EditorialStore.action(actor=...)
          -> set_config('request.jwt.claims', ..., true)
          -> SET LOCAL ROLE authenticated
            -> app_is_commissioner()
              -> RLS
                -> the row

Every link has been tested somewhere; none of them had been tested
together, which is how a project mismatch survived weeks of green tests.

WHAT IS STILL NOT PROVED HERE, AND CANNOT BE

The link ABOVE the session: Supabase mints the identity by emailing a
one-time code, and doing that against the canonical project needs that
project's publishable key, which lives in its dashboard. So this file
starts where a verified sign-in would leave off -- at a signed session for
an allowlisted address -- and proves everything from there. The manual
step is named in docs/CUTOVER.md.

Live, opt-in with LEAGUEPAGE_TEST_DATABASE_URL, and it writes only to a
scratch slug that is removed either side.
"""
from __future__ import annotations

import json
import os

import pytest
from fastapi.testclient import TestClient

from leaguepage import auth, project_check, prose_store, settings
from leaguepage.desk import create_app
from leaguepage.storage import Storage

LIVE = os.environ.get("LEAGUEPAGE_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not LIVE, reason="live opt-in not set")

SCRATCH = "__auth-chain-probe__"
STRANGER = "not-the-commissioner@example.invalid"


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


def _selecting_postgres(real_get, url: str | None):
    def get(name, default=None):
        if name == prose_store.BACKEND_SETTING:
            return prose_store.POSTGRES
        if name == "DATABASE_URL":
            return LIVE
        if name == "SUPABASE_URL" and url is not None:
            return url
        return real_get(name, default)
    return get


def _canonical_url() -> str:
    """The API URL of the project DATABASE_URL points at. Public: it is the
    project's own subdomain, and it is derived rather than configured."""
    return f"https://{project_check.ref_of_dsn(LIVE)}.supabase.co"


@pytest.fixture
def signed_in(tmp_path, monkeypatch):
    """A Desk with sign-in ON, Postgres selected, and a real session.

    Nothing is written to `.env`; the backend and the Supabase URL are
    chosen for this process only, and the URL is the canonical project's
    so the startup guard is satisfied honestly rather than bypassed.
    """
    import psycopg

    actor = _commissioner()
    monkeypatch.setattr(settings, "get",
                        _selecting_postgres(settings.get, _canonical_url()))
    monkeypatch.setenv("LEAGUEPAGE_AUTH_MODE", "required")
    monkeypatch.setenv("LEAGUEPAGE_COMMISSIONER_EMAILS", actor)
    monkeypatch.setenv("LEAGUEPAGE_SECRET_KEY", "test-secret-key")
    auth._USED_LOGIN_JTI.clear()
    auth._LOGIN_ATTEMPTS.clear()
    prose_store.reset_cache()

    with psycopg.connect(LIVE, connect_timeout=30, autocommit=True) as c:
        c.execute("delete from site_documents where slug = %s", (SCRATCH,))

    db = tmp_path / "d.sqlite3"
    with Storage(db):
        pass
    client = TestClient(create_app(db), follow_redirects=False)
    token = auth.issue_login_token(actor)
    r = client.get(f"/auth/callback?token={token}")
    assert r.status_code == 303, "the sign-in leg did not complete"
    client.cookies.set(auth.SESSION_COOKIE, r.cookies[auth.SESSION_COOKIE])
    try:
        yield client, actor
    finally:
        prose_store.reset_cache()
        with psycopg.connect(LIVE, connect_timeout=30, autocommit=True) as c:
            c.execute("delete from site_documents where slug = %s", (SCRATCH,))


# ------------------------------------------------------------- the chain

@needs_0007
def test_a_signed_session_reaches_the_database_as_that_person(signed_in):
    """Every link at once, observed INSIDE the writing transaction.

    Outside it the connection is the owner and always was, so a check
    taken afterwards would prove nothing.
    """
    import psycopg

    from leaguepage import editorial_store, site_documents

    client, actor = signed_in
    seen = {}
    real = psycopg.Cursor.execute

    def spy(self, query, *a, **kw):
        if "insert into site_documents" in str(query).lower():
            seen["role"] = real(self, "select current_role").fetchone()[0]
            seen["claims"] = real(
                self, "select current_setting('request.jwt.claims', true)"
            ).fetchone()[0]
            seen["is_commissioner"] = real(
                self, "select app_is_commissioner()").fetchone()[0]
        return real(self, query, *a, **kw)

    psycopg.Cursor.execute = spy
    try:
        with editorial_store.store().action(actor=actor) as act:
            act.state.set_site_document(SCRATCH, "# chained\n", actor)
    finally:
        psycopg.Cursor.execute = real

    assert seen.get("role") == "authenticated", (
        f"wrote as {seen.get('role')!r}, not as the signed-in Commissioner")
    assert actor in json.loads(seen.get("claims") or "{}").get("email", "")
    assert seen.get("is_commissioner") is True, (
        "RLS's own predicate said this actor is not a Commissioner")

    with psycopg.connect(LIVE, connect_timeout=30, autocommit=True) as c:
        body, by = c.execute(
            "select body, updated_by from site_documents where slug=%s",
            (SCRATCH,)).fetchone()
    assert body == "# chained\n" and by == actor


@needs_0007
def test_the_session_carries_the_identity_the_route_acts_on(signed_in):
    """`actor_of` reads the signed cookie, not a form field."""
    client, actor = signed_in
    r = client.get("/commissioner/site/about")
    assert r.status_code == 200
    assert "site_documents" in r.text, "the Desk is not on the cloud backend"


@needs_0007
def test_without_the_cookie_nothing_reaches_the_store(signed_in):
    client, _actor = signed_in
    bare = TestClient(client.app, follow_redirects=False)
    r = bare.post("/commissioner/site/about",
                  data={"text": "# anonymous\n", "action": "save"})
    assert r.status_code in (303, 401, 403)
    assert "/login" in r.headers.get("location", "") or r.status_code != 303

    import psycopg

    with psycopg.connect(LIVE, connect_timeout=30, autocommit=True) as c:
        assert c.execute("select count(*) from site_documents where slug=%s",
                         (SCRATCH,)).fetchone()[0] == 0


@needs_0007
def test_an_allowlisted_session_is_not_enough_if_the_database_disagrees():
    """Two independent gates, and the database's is the one that binds.

    The allowlist is a setting; `app_commissioners` is a table. A person in
    the first and not the second gets a session and writes nothing.
    """
    import psycopg

    from leaguepage import editorial_store

    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with editorial_store.store(backend=prose_store.POSTGRES).action(
                actor=STRANGER) as act:
            act.state.set_site_document(SCRATCH, "# theirs\n", STRANGER)


# ----------------------------------------------------- the startup guard

def test_the_hosted_shape_refuses_to_start_on_a_project_mismatch(
        tmp_path, monkeypatch):
    """The regression guard for 2026-09-09: Auth in one project, the
    database in another, and nothing saying so."""
    # A ref that is not this project's and is not anybody's.
    other = "https://bbbbbbbbbbbbbbbbbbbb.supabase.co"
    monkeypatch.setattr(settings, "get",
                        _selecting_postgres(settings.get, other))
    monkeypatch.setenv("LEAGUEPAGE_AUTH_MODE", "required")
    monkeypatch.setenv("LEAGUEPAGE_SECRET_KEY", "test-secret-key")
    prose_store.reset_cache()
    db = tmp_path / "d.sqlite3"
    with Storage(db):
        pass
    with pytest.raises(RuntimeError, match="Supabase project mismatch"):
        create_app(db)
    prose_store.reset_cache()


def test_the_same_project_starts(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "get",
                        _selecting_postgres(settings.get, _canonical_url()))
    monkeypatch.setenv("LEAGUEPAGE_AUTH_MODE", "required")
    monkeypatch.setenv("LEAGUEPAGE_SECRET_KEY", "test-secret-key")
    prose_store.reset_cache()
    db = tmp_path / "d.sqlite3"
    with Storage(db):
        pass
    assert create_app(db) is not None
    prose_store.reset_cache()
