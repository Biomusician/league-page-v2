"""Commissioner authentication: passwordless magic link, server-side allowlist.

Design constraints this satisfies:
  - single-commissioner application; NO public sign-up, ever
  - authorization is decided server-side from a configured allowlist; a
    browser-supplied email, role, or hidden field is never trusted
  - a rejected address learns nothing about whether it is on the allowlist
  - sessions are signed, expiring, HttpOnly, SameSite=Lax, Secure in prod
  - CSRF protection for authenticated mutations

Tokens are HMAC-SHA256 over a compact JSON payload — stdlib only, no new
dependency. Two token kinds, deliberately separated so a long-lived session
cookie can never be replayed as a login link or vice versa:

  login   short TTL, single-use (jti recorded), arrives by email
  session longer TTL, cookie only

Mode is environment-driven so the existing local Desk keeps working
unchanged while the hosted deployment demands authentication:

  LEAGUEPAGE_AUTH_MODE=off       (default) localhost fallback, no auth
  LEAGUEPAGE_AUTH_MODE=required  hosted; every private route needs a session
  LEAGUEPAGE_COMMISSIONER_EMAILS comma-separated allowlist
  LEAGUEPAGE_SECRET_KEY          signing key (required when mode=required)
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass

SESSION_COOKIE = "lp_session"
LOGIN_TTL = 15 * 60           # magic link validity
SESSION_TTL = 14 * 24 * 3600  # two weeks
KIND_LOGIN = "login"
KIND_SESSION = "session"

# single-user app: a small in-process counter is proportionate rate limiting
_LOGIN_ATTEMPTS: dict[str, list[float]] = {}
_USED_LOGIN_JTI: dict[str, float] = {}
LOGIN_RATE_LIMIT = 5          # per window
LOGIN_RATE_WINDOW = 15 * 60


class AuthError(Exception):
    """Token could not be verified. The message is for logs, never for users:
    callers show a single generic failure so nothing is disclosed."""


def auth_mode() -> str:
    return (os.environ.get("LEAGUEPAGE_AUTH_MODE") or "off").strip().lower()


def auth_required() -> bool:
    return auth_mode() == "required"


def allowlist() -> set[str]:
    raw = os.environ.get("LEAGUEPAGE_COMMISSIONER_EMAILS") or ""
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def is_allowed(email: str) -> bool:
    return (email or "").strip().lower() in allowlist()


def _secret() -> bytes:
    key = os.environ.get("LEAGUEPAGE_SECRET_KEY")
    if key:
        return key.encode("utf-8")
    if auth_required():
        raise AuthError("LEAGUEPAGE_SECRET_KEY is required when auth is on")
    # local fallback mode: ephemeral key, sessions die with the process
    global _EPHEMERAL
    try:
        return _EPHEMERAL
    except NameError:
        _EPHEMERAL = secrets.token_bytes(32)
        return _EPHEMERAL


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sign(payload: dict, ttl: int) -> str:
    body = dict(payload)
    body["exp"] = int(time.time()) + ttl
    body.setdefault("jti", secrets.token_urlsafe(8))
    raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    mac = hmac.new(_secret(), raw, hashlib.sha256).digest()
    return f"{_b64(raw)}.{_b64(mac)}"


def unsign(token: str, *, kind: str) -> dict:
    try:
        body_b64, mac_b64 = (token or "").split(".", 1)
        raw = _unb64(body_b64)
        expected = hmac.new(_secret(), raw, hashlib.sha256).digest()
    except Exception as exc:  # malformed input is just a failure
        raise AuthError(f"malformed token: {type(exc).__name__}") from None
    if not hmac.compare_digest(expected, _unb64(mac_b64)):
        raise AuthError("bad signature")
    body = json.loads(raw)
    if body.get("kind") != kind:
        raise AuthError(f"wrong token kind: {body.get('kind')} != {kind}")
    if int(body.get("exp", 0)) < time.time():
        raise AuthError("expired")
    return body


# ------------------------------------------------------------------ login

def rate_limited(key: str) -> bool:
    now = time.time()
    hits = [t for t in _LOGIN_ATTEMPTS.get(key, []) if now - t < LOGIN_RATE_WINDOW]
    _LOGIN_ATTEMPTS[key] = hits + [now]
    return len(hits) >= LOGIN_RATE_LIMIT


def issue_login_token(email: str) -> str:
    """Caller MUST check is_allowed() first; this only mints."""
    return sign({"kind": KIND_LOGIN, "email": email.strip().lower()}, LOGIN_TTL)


def consume_login_token(token: str) -> str:
    """Verify a magic link and burn it. Returns the email.

    Single-use: the jti is remembered until it would have expired anyway, so
    a link captured from an inbox cannot be replayed."""
    body = unsign(token, kind=KIND_LOGIN)
    jti = body.get("jti", "")
    now = time.time()
    for old, exp in list(_USED_LOGIN_JTI.items()):
        if exp < now:
            _USED_LOGIN_JTI.pop(old, None)
    if jti in _USED_LOGIN_JTI:
        raise AuthError("login token already used")
    email = body.get("email", "")
    # the allowlist is re-checked at redemption: removing an address must
    # invalidate links already sitting in a mailbox
    if not is_allowed(email):
        raise AuthError("email no longer allowed")
    _USED_LOGIN_JTI[jti] = float(body.get("exp", now))
    return email


# ---------------------------------------------------------------- session

@dataclass(frozen=True)
class Session:
    email: str
    csrf: str


def create_session(email: str) -> str:
    return sign({"kind": KIND_SESSION, "email": email.strip().lower(),
                 "csrf": secrets.token_urlsafe(16)}, SESSION_TTL)


def read_session(cookie: str | None) -> Session | None:
    if not cookie:
        return None
    try:
        body = unsign(cookie, kind=KIND_SESSION)
    except AuthError:
        return None
    email = body.get("email", "")
    # allowlist is authoritative on every request, not just at login
    if not is_allowed(email):
        return None
    return Session(email=email, csrf=body.get("csrf", ""))


def cookie_kwargs() -> dict:
    """Secure only in a real deployment: localhost fallback is plain HTTP."""
    return {"httponly": True, "samesite": "lax", "path": "/",
            "secure": auth_required(), "max_age": SESSION_TTL}


def check_csrf(session: Session | None, submitted: str | None) -> bool:
    if not auth_required():
        return True
    if session is None or not session.csrf or not submitted:
        return False
    return hmac.compare_digest(session.csrf, submitted)
