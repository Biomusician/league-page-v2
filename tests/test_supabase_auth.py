"""Supabase-backed sign-in, and the secret boundary around it.

No network: the Supabase HTTP calls are stubbed. What is under test is the
authorization logic and the boundary rules, not Supabase itself.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from leaguepage import auth, settings, supabase_client
from leaguepage.config import get_league
from leaguepage.desk import create_app
from leaguepage.storage import Storage

from fixtures import populate_league

COMMISH = "commish@example.com"
STRANGER = "valid-supabase-user@example.com"


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    monkeypatch.setenv("LEAGUEPAGE_AUTH_MODE", "required")
    monkeypatch.setenv("LEAGUEPAGE_COMMISSIONER_EMAILS", COMMISH)
    monkeypatch.setenv("LEAGUEPAGE_SECRET_KEY", "test-secret-key")
    monkeypatch.setenv(settings.SUPABASE_URL, "https://proj.supabase.co")
    monkeypatch.setenv(settings.SUPABASE_PUBLISHABLE_KEY, "pk_test_value")
    auth._USED_LOGIN_JTI.clear()
    auth._LOGIN_ATTEMPTS.clear()
    db = tmp_path / "d.sqlite3"
    with Storage(db) as s:
        populate_league(s, get_league("surfeit"), teams=10, rounds=3)
        populate_league(s, get_league("disco"), teams=12, rounds=3)
        s.set_meta("current_week", "1")
    return TestClient(create_app(db), follow_redirects=False)


# ------------------------------------------------------------ config

def test_settings_never_reveal_secret_values(monkeypatch):
    monkeypatch.setenv(settings.SUPABASE_SECRET_KEY, "super-secret-value")
    monkeypatch.setenv(settings.DATABASE_URL, "postgresql://u:pw@host/db")
    rendered = str(settings.describe())
    assert "super-secret-value" not in rendered
    assert "pw@host" not in rendered
    for row in settings.describe():
        if row["name"] in (settings.SUPABASE_SECRET_KEY, settings.DATABASE_URL):
            assert row["secret"] and row["hint"] in ("", "set")


def test_env_file_does_not_override_real_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("LP_TEST_NAME", "from-environment")
    f = tmp_path / ".env"
    f.write_text("LP_TEST_NAME=from-file\nLP_OTHER=from-file\n", encoding="utf-8")
    settings.load_env(f, force=True)
    import os
    assert os.environ["LP_TEST_NAME"] == "from-environment"
    assert os.environ["LP_OTHER"] == "from-file"


# ------------------------------------------------------- sign-in flow

def test_otp_is_only_sent_to_allowlisted_addresses(app_client, monkeypatch):
    sent = []
    monkeypatch.setattr(supabase_client, "send_email_otp",
                        lambda email: sent.append(email))
    good = app_client.post("/auth/request", data={"email": COMMISH})
    bad = app_client.post("/auth/request", data={"email": STRANGER})
    assert good.status_code == bad.status_code == 303
    assert good.headers["location"] == bad.headers["location"]
    assert sent == [COMMISH]


def test_pending_email_is_carried_in_a_cookie_not_the_url(app_client, monkeypatch):
    """An email address must not land in browser history or access logs."""
    monkeypatch.setattr(supabase_client, "send_email_otp", lambda email: None)
    r = app_client.post("/auth/request", data={"email": COMMISH})
    assert COMMISH not in r.headers["location"]
    cookie = r.cookies.get("lp_pending")
    # The cookie is signed (tamper-proof) and HttpOnly, but base64 is
    # encoding rather than encryption: the point is that the address stays
    # out of the URL, not that it is secret from its own owner's browser.
    assert cookie and auth.unsign(cookie, kind="pending")["email"] == COMMISH
    tampered = cookie.split(".")[0] + "." + "A" * 20
    with pytest.raises(auth.AuthError):
        auth.unsign(tampered, kind="pending")


def test_valid_supabase_user_not_on_allowlist_is_refused(app_client, monkeypatch):
    """The whole point of the second gate: Supabase says who you are, the
    allowlist says whether you may use this application."""
    monkeypatch.setattr(supabase_client, "verify_email_otp",
                        lambda email, code: STRANGER)
    r = app_client.post("/auth/verify",
                        data={"email": STRANGER, "code": "123456"})
    assert r.status_code == 303 and "error=1" in r.headers["location"]
    assert auth.SESSION_COOKIE not in r.cookies


def test_successful_otp_creates_a_commissioner_session(app_client, monkeypatch):
    monkeypatch.setattr(supabase_client, "verify_email_otp",
                        lambda email, code: COMMISH)
    r = app_client.post("/auth/verify",
                        data={"email": COMMISH, "code": "123456"})
    assert r.status_code == 303
    cookie = r.cookies[auth.SESSION_COOKIE]
    assert auth.read_session(cookie).email == COMMISH
    app_client.cookies.set(auth.SESSION_COOKIE, cookie)
    assert app_client.get("/commissioner",
                          headers={"accept": "text/html"}).status_code == 200


def test_identity_comes_from_supabase_not_the_posted_field(app_client, monkeypatch):
    """A caller cannot become the Commissioner by editing the email field:
    authorization uses the address Supabase returned."""
    monkeypatch.setattr(supabase_client, "verify_email_otp",
                        lambda email, code: STRANGER)
    r = app_client.post("/auth/verify",
                        data={"email": COMMISH, "code": "123456"})
    assert "error=1" in r.headers["location"]


def test_bad_code_is_refused_without_disclosing_why(app_client, monkeypatch):
    def boom(email, code):
        raise supabase_client.SupabaseError("verify rejected: 403")
    monkeypatch.setattr(supabase_client, "verify_email_otp", boom)
    r = app_client.post("/auth/verify", data={"email": COMMISH, "code": "000000"})
    assert r.status_code == 303 and "error=1" in r.headers["location"]
    assert "403" not in r.text


def test_verify_endpoint_is_rate_limited(app_client, monkeypatch):
    monkeypatch.setattr(supabase_client, "verify_email_otp",
                        lambda email, code: COMMISH)
    for _ in range(auth.LOGIN_RATE_LIMIT + 2):
        last = app_client.post("/auth/verify",
                               data={"email": COMMISH, "code": "123456"})
    assert "error=1" in last.headers["location"]


def test_probe_reports_cleanly_when_unconfigured(monkeypatch):
    monkeypatch.delenv(settings.SUPABASE_URL, raising=False)
    monkeypatch.delenv(settings.SUPABASE_PUBLISHABLE_KEY, raising=False)
    monkeypatch.setattr(settings, "ENV_FILE", __import__("pathlib").Path("nope"))
    settings.load_env(force=True)
    report = supabase_client.probe()
    assert report["configured"] is False and report["reachable"] is False


# --------------------------------------------------- the secret boundary

def test_no_supabase_credential_reaches_the_browser(app_client, monkeypatch):
    """Our OTP exchange is server-side, so no key of any class should appear
    in rendered HTML — not even the browser-safe publishable one."""
    monkeypatch.setattr(supabase_client, "verify_email_otp",
                        lambda email, code: COMMISH)
    cookie = app_client.post(
        "/auth/verify", data={"email": COMMISH, "code": "1"}
    ).cookies[auth.SESSION_COOKIE]
    app_client.cookies.set(auth.SESSION_COOKIE, cookie)
    for path in ("/login", "/commissioner"):
        body = app_client.get(path, headers={"accept": "text/html"}).text
        assert "pk_test_value" not in body
        assert "supabase.co" not in body


def test_public_build_carries_no_supabase_or_auth_material(tmp_path, monkeypatch):
    import leaguepage.issue_builder as ib
    import leaguepage.matchup_packet as mp
    from leaguepage.site_build import build_site

    monkeypatch.setattr(ib, "EDITORIAL_DIR", tmp_path / "editorial")
    monkeypatch.setattr(mp, "EDITORIAL_DIR", tmp_path / "editorial")
    monkeypatch.setattr(mp, "load_managers", lambda: {})
    db = tmp_path / "t.sqlite3"
    with Storage(db) as s:
        populate_league(s, get_league("surfeit"), teams=10, rounds=3)
        populate_league(s, get_league("disco"), teams=12, rounds=3)
        s.set_meta("current_week", "1")
        build_site(s, out_dir=tmp_path / "dist",
                   published_dir=tmp_path / "published",
                   editorial_dir=tmp_path / "editorial")
    forbidden = ("supabase", "SUPABASE_", "apikey", "service_role",
                 "lp_session", "auth/verify", "app_commissioners")
    for f in (tmp_path / "dist").rglob("*"):
        if f.is_file() and f.suffix in (".html", ".js", ".json"):
            body = f.read_text(encoding="utf-8", errors="ignore").lower()
            for token in forbidden:
                assert token.lower() not in body, f"{token} leaked into {f.name}"


def test_migration_sql_locks_out_anon_and_is_not_any_authenticated_user():
    from leaguepage.config import REPO_ROOT

    sql = (REPO_ROOT / "migrations" / "0001_commissioner_state.sql").read_text(
        encoding="utf-8")
    assert "force row level security" in sql
    assert "revoke all on %I from anon" in sql
    # the policy must consult the allowlist, not merely "authenticated"
    assert "app_is_commissioner()" in sql
    assert "using (true)" not in sql.lower()
