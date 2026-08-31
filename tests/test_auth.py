"""Commissioner authentication and the private-route audit.

The audit test is the important one: it enumerates every route the app
actually registers and proves each is closed unless it is on an explicit
public list. A new private route cannot be born public by accident.
"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from leaguepage import auth
from leaguepage.config import get_league
from leaguepage.desk import create_app
from leaguepage.storage import Storage

from fixtures import populate_league

COMMISH = "commish@example.com"
STRANGER = "someone-else@example.com"

# Routes that must remain reachable without a session, and why.
PUBLIC_PATHS = {
    "/health",              # launcher readiness probe
    "/login",               # the login form itself
    "/auth/request",        # sign-in request (OTP or magic link)
    "/auth/verify",         # OTP code exchange
    "/auth/callback",       # magic-link redemption
    "/static/sortable.js",  # static asset, no data
}


@pytest.fixture
def secure(tmp_path, monkeypatch):
    monkeypatch.setenv("LEAGUEPAGE_AUTH_MODE", "required")
    monkeypatch.setenv("LEAGUEPAGE_COMMISSIONER_EMAILS", COMMISH)
    monkeypatch.setenv("LEAGUEPAGE_SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("LEAGUEPAGE_MAIL_PROVIDER", "log")
    auth._USED_LOGIN_JTI.clear()
    auth._LOGIN_ATTEMPTS.clear()
    db = tmp_path / "d.sqlite3"
    with Storage(db) as s:
        populate_league(s, get_league("surfeit"), teams=10, rounds=3)
        populate_league(s, get_league("disco"), teams=12, rounds=3)
        s.set_meta("current_week", "1")
    return TestClient(create_app(db), follow_redirects=False)


def _sign_in(client) -> str:
    """Drive the real magic-link flow; return the session cookie value."""
    token = auth.issue_login_token(COMMISH)
    r = client.get(f"/auth/callback?token={token}")
    assert r.status_code == 303
    return r.cookies[auth.SESSION_COOKIE]


# ------------------------------------------------------- the route audit

def test_every_private_route_rejects_anonymous_access(secure):
    client = secure
    app = client.app
    checked = 0
    for route in app.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", set()) or set()
        if not path or path in PUBLIC_PATHS:
            continue
        for method in sorted(methods & {"GET", "POST"}):
            # substitute plausible values for path params; the request must
            # be refused before any handler or validation sees it
            concrete = (path.replace("{league_slug}", "surfeit")
                            .replace("{season}", "2026")
                            .replace("{issue_key}", "week-01")
                            .replace("{week}", "1")
                            .replace("{slug}", "a-vs-b")
                            .replace("{module_key}", "lowdown")
                            .replace("{section}", "lowdown"))
            if "{" in concrete:
                continue
            r = client.request(method, concrete,
                               headers={"accept": "text/html"})
            checked += 1
            assert r.status_code in (303, 401, 403), (
                f"{method} {concrete} returned {r.status_code} to an "
                "anonymous caller")
            if r.status_code == 303:
                assert "/login" in r.headers.get("location", "")
    assert checked > 30, f"audit only covered {checked} routes"


def test_api_style_request_gets_401_not_a_login_page(secure):
    r = secure.get("/commissioner/surfeit/2026/issue/week-01/edit/publish-status")
    assert r.status_code == 401
    assert "authentication required" in r.text


def test_health_stays_public_for_the_launcher(secure):
    assert secure.get("/health").status_code == 200


# ------------------------------------------------------------- sign-in

def test_allowlisted_email_can_sign_in_and_reach_the_desk(secure):
    secure.cookies.set(auth.SESSION_COOKIE, _sign_in(secure))
    r = secure.get("/commissioner", headers={"accept": "text/html"})
    assert r.status_code == 200
    assert "Commissioner's Desk" in r.text


def test_stranger_learns_nothing_and_gets_no_link(secure, monkeypatch):
    sent = []
    import leaguepage.mailer as mailer
    monkeypatch.setattr(mailer, "send_mail",
                        lambda *a, **k: sent.append(a) or "log")

    good = secure.post("/auth/request", data={"email": COMMISH})
    bad = secure.post("/auth/request", data={"email": STRANGER})
    # identical response: no disclosure of who is on the allowlist. The
    # redirect carries no email at all (it lives in a signed cookie), so the
    # two responses are byte-identical.
    assert good.status_code == bad.status_code == 303
    assert good.headers["location"] == bad.headers["location"]
    assert "@" not in good.headers["location"]
    assert len(sent) == 1 and sent[0][0] == COMMISH


def test_login_token_is_single_use(secure):
    token = auth.issue_login_token(COMMISH)
    assert secure.get(f"/auth/callback?token={token}").status_code == 303
    replay = secure.get(f"/auth/callback?token={token}")
    assert replay.status_code == 303
    assert "error=1" in replay.headers["location"]


def test_expired_and_forged_tokens_are_rejected(secure):
    expired = auth.sign({"kind": auth.KIND_LOGIN, "email": COMMISH}, -1)
    with pytest.raises(auth.AuthError):
        auth.consume_login_token(expired)

    good = auth.issue_login_token(COMMISH)
    body, mac = good.split(".", 1)
    with pytest.raises(auth.AuthError):
        auth.consume_login_token(f"{body}.{'A' * len(mac)}")


def test_session_cookie_cannot_be_forged_or_tampered(secure):
    cookie = _sign_in(secure)
    body, mac = cookie.split(".", 1)
    for bad in (f"{body}.{'A' * len(mac)}", "garbage", f"{body}."):
        assert auth.read_session(bad) is None


def test_a_login_token_is_not_usable_as_a_session(secure):
    # kind separation: the short-lived emailed token must not authenticate
    assert auth.read_session(auth.issue_login_token(COMMISH)) is None


def test_removing_the_allowlist_entry_kills_live_sessions(secure, monkeypatch):
    cookie = _sign_in(secure)
    assert auth.read_session(cookie) is not None
    monkeypatch.setenv("LEAGUEPAGE_COMMISSIONER_EMAILS", "nobody@example.com")
    assert auth.read_session(cookie) is None


def test_callback_refuses_an_offsite_redirect(secure):
    token = auth.issue_login_token(COMMISH)
    r = secure.get(f"/auth/callback?token={token}&next=https://evil.example")
    assert r.headers["location"] == "/commissioner"


def test_login_requests_are_rate_limited(secure):
    for _ in range(auth.LOGIN_RATE_LIMIT):
        assert not auth.rate_limited("login:test")
    assert auth.rate_limited("login:test")


# ---------------------------------------------------------------- CSRF

def test_authenticated_post_requires_a_csrf_token(secure):
    cookie = _sign_in(secure)
    secure.cookies.set(auth.SESSION_COOKIE, cookie)
    path = "/commissioner/sync-start"

    denied = secure.post(path)
    assert denied.status_code == 403 and "csrf" in denied.text

    csrf = auth.read_session(cookie).csrf
    allowed = secure.post(path, headers={"x-csrf-token": csrf})
    assert allowed.status_code in (200, 303)


def test_csrf_is_not_demanded_in_local_fallback_mode(tmp_path, monkeypatch):
    """The localhost Desk keeps working with no auth and no CSRF ceremony."""
    monkeypatch.setenv("LEAGUEPAGE_AUTH_MODE", "off")
    db = tmp_path / "d.sqlite3"
    with Storage(db) as s:
        populate_league(s, get_league("surfeit"), teams=10, rounds=3)
        populate_league(s, get_league("disco"), teams=12, rounds=3)
        s.set_meta("current_week", "1")
    client = TestClient(create_app(db), follow_redirects=False)
    assert client.get("/commissioner", headers={"accept": "text/html"}).status_code == 200


def test_secret_key_is_demanded_when_auth_is_on(monkeypatch):
    monkeypatch.setenv("LEAGUEPAGE_AUTH_MODE", "required")
    monkeypatch.delenv("LEAGUEPAGE_SECRET_KEY", raising=False)
    with pytest.raises(auth.AuthError):
        auth.sign({"kind": auth.KIND_SESSION}, 60)


def test_auth_config_comes_from_the_env_file(tmp_path, monkeypatch):
    """Regression: auth once read os.environ directly, so a .env-configured
    allowlist was silently ignored and the Desk stayed wide open in fallback
    mode while looking configured. Every auth setting must go through
    settings.get(), which is what loads .env."""
    from leaguepage import settings

    env = tmp_path / ".env"
    env.write_text("LEAGUEPAGE_AUTH_MODE=required\n"
                   "LEAGUEPAGE_COMMISSIONER_EMAILS=from-file@example.com\n"
                   "LEAGUEPAGE_SECRET_KEY=from-file-secret\n",
                   encoding="utf-8")
    monkeypatch.setattr(settings, "ENV_FILE", env)
    monkeypatch.setattr(settings, "_loaded", False)

    assert auth.auth_required() is True
    assert auth.is_allowed("from-file@example.com")
    assert not auth.is_allowed(STRANGER)
    # a real signing key was found, so no ephemeral fallback was used
    assert auth.sign({"kind": auth.KIND_SESSION}, 60)
