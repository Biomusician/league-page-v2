"""Supabase integration: identity provider only, driven from the server.

Why the REST endpoints and not the supabase-py SDK: this app needs exactly
three calls (health, send OTP, verify OTP). httpx is already a dependency;
the SDK is not, and would not save real work. Using the REST API directly
also keeps the flow server-side, which is the security point below.

WHY EMAIL OTP RATHER THAN MAGIC LINK
  A magic link needs Redirect URLs registered in the Supabase dashboard and
  introduces redirect-handling surface. A six-digit OTP needs neither, so
  authentication is testable locally today and the hosted URL can be added
  later without changing this code. Magic links can be layered on afterwards.

WHERE THE KEY LIVES
  Supabase's publishable key is browser-safe by design, but this app never
  ships it to the browser: the server sends the OTP and the server verifies
  it. The browser only ever posts an email address and a six-digit code.
  So the publishable key stays server-side, and no Supabase credential of
  any class appears in rendered HTML or JavaScript.

WHAT THIS MODULE DOES NOT DO
  It does not authorize anybody. Supabase answers "is this person who they
  say they are"; the Commissioner allowlist in leaguepage.auth answers "may
  they use this application", and it is checked independently both before an
  OTP is sent and after it is verified.
"""
from __future__ import annotations

import httpx

from leaguepage import settings

TIMEOUT = 15.0


class SupabaseError(Exception):
    """Any failure talking to Supabase. Messages are for logs; callers must
    show the user a single generic failure so nothing is disclosed."""


def config() -> dict:
    return {"url": (settings.get(settings.SUPABASE_URL) or "").rstrip("/"),
            "key": settings.get(settings.SUPABASE_PUBLISHABLE_KEY) or ""}


def configured() -> bool:
    c = config()
    return bool(c["url"] and c["key"])


def _headers(key: str) -> dict:
    return {"apikey": key, "Authorization": f"Bearer {key}",
            "Content-Type": "application/json"}


def probe() -> dict:
    """Connectivity and capability check using only the publishable key.
    Never raises: returns a structured report for diagnostics."""
    c = config()
    report = {"configured": configured(), "url_set": bool(c["url"]),
              "key_set": bool(c["key"]), "reachable": False,
              "auth_health": None, "key_accepted": None, "detail": ""}
    if not report["configured"]:
        report["detail"] = "SUPABASE_URL / SUPABASE_PUBLISHABLE_KEY not set"
        return report
    try:
        with httpx.Client(timeout=TIMEOUT) as client:
            h = client.get(f"{c['url']}/auth/v1/health")
            report["reachable"] = True
            report["auth_health"] = h.status_code
            # /auth/v1/settings requires a valid apikey: a 200 proves the
            # publishable key is accepted by this project.
            s = client.get(f"{c['url']}/auth/v1/settings",
                           headers=_headers(c["key"]))
            report["key_accepted"] = s.status_code == 200
            if s.status_code == 200:
                data = s.json()
                report["external_email_enabled"] = bool(
                    (data.get("external") or {}).get("email", True))
                report["mailer_autoconfirm"] = data.get("mailer_autoconfirm")
                report["disable_signup"] = data.get("disable_signup")
            else:
                report["detail"] = f"settings returned {s.status_code}"
    except Exception as exc:
        report["detail"] = f"{type(exc).__name__}: {exc}"
    return report


def send_email_otp(email: str) -> None:
    """Ask Supabase to email a sign-in code.

    `should_create_user` is True, and that deserves an explanation because
    it looks like sign-up. It is not:

      - This function is only ever reached after leaguepage.auth.is_allowed()
        has passed, so a code is only ever sent to an address the
        Commissioner allowlist already authorizes.
      - A Supabase user record grants nothing on its own. Authorization is
        decided twice more: the allowlist is re-checked against the address
        Supabase returns at verification, and Row Level Security refuses
        every table to anyone absent from app_commissioners.
      - Without it a brand-new project cannot bootstrap at all: Supabase
        answers `otp_disabled / Signups not allowed for otp` because no user
        record exists yet, which is a chicken-and-egg for the first sign-in.

    So the identity record is created on first sign-in; permission to use
    the application remains entirely ours.
    """
    c = config()
    if not configured():
        raise SupabaseError("supabase not configured")
    payload = {"email": email, "options": {"should_create_user": True}}
    try:
        with httpx.Client(timeout=TIMEOUT) as client:
            r = client.post(f"{c['url']}/auth/v1/otp",
                            headers=_headers(c["key"]), json=payload)
    except Exception as exc:
        raise SupabaseError(f"otp request failed: {type(exc).__name__}") from None
    if r.status_code >= 400:
        raise SupabaseError(f"otp rejected: {r.status_code} {r.text[:200]}")


def verify_email_otp(email: str, code: str) -> str:
    """Verify a code and return the email Supabase says was authenticated.

    The returned address comes from Supabase's own user record, not from the
    browser's form field, so a caller cannot claim to be somebody else by
    editing the posted email.
    """
    c = config()
    if not configured():
        raise SupabaseError("supabase not configured")
    payload = {"email": email, "token": (code or "").strip(), "type": "email"}
    try:
        with httpx.Client(timeout=TIMEOUT) as client:
            r = client.post(f"{c['url']}/auth/v1/verify",
                            headers=_headers(c["key"]), json=payload)
    except Exception as exc:
        raise SupabaseError(f"verify failed: {type(exc).__name__}") from None
    if r.status_code >= 400:
        raise SupabaseError(f"verify rejected: {r.status_code}")
    data = r.json()
    verified = ((data.get("user") or {}).get("email") or "").strip().lower()
    if not verified:
        raise SupabaseError("verify response carried no user email")
    return verified
