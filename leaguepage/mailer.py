"""Outbound mail for the Commissioner Desk.

One seam, two backends. Local fallback writes the link to the console and
logs/login-links.log so the localhost Desk needs no provider at all; the
hosted deployment sets LEAGUEPAGE_MAIL_PROVIDER=resend and the same call
sends a real message.

    LEAGUEPAGE_MAIL_PROVIDER   log (default) | resend
    LEAGUEPAGE_MAIL_FROM       e.g. "Desk <desk@your-domain>"
    RESEND_API_KEY             provider secret; server-side only, never logged

Nothing here ever logs the API key or the full magic-link token in the
hosted path — a login link is a bearer credential for its 15 minutes.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import urllib.request
from pathlib import Path

from leaguepage.config import REPO_ROOT

LOG_PATH = REPO_ROOT / "logs" / "login-links.log"


def _provider() -> str:
    return (os.environ.get("LEAGUEPAGE_MAIL_PROVIDER") or "log").strip().lower()


def _from_address() -> str:
    return os.environ.get("LEAGUEPAGE_MAIL_FROM") or "League-Page Desk <desk@localhost>"


def send_mail(to: str, subject: str, text: str,
              reply_to: str | None = None) -> str:
    """Returns a short status string for logs. Never raises on delivery
    failure in local mode; the caller must not leak provider errors to the
    browser (they would confirm whether an address is on the allowlist)."""
    provider = _provider()
    if provider == "resend":
        key = os.environ.get("RESEND_API_KEY")
        if not key:
            return "resend: missing RESEND_API_KEY"
        body = {"from": _from_address(), "to": [to], "subject": subject,
                "text": text}
        if reply_to:
            body["reply_to"] = reply_to
        req = urllib.request.Request(
            "https://api.resend.com/emails",
            data=json.dumps(body).encode("utf-8"),
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json"},
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return f"resend: {resp.status}"
        except Exception as exc:
            return f"resend: failed ({type(exc).__name__})"

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().isoformat(timespec="seconds")
    with LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(f"\n[{stamp}] to={to}\nsubject={subject}\n{text}\n")
    print(f"\n  [mail:log] {subject} -> {to}\n  {text}\n", flush=True)
    return "log"


def deliver_login_link(email: str, url: str) -> str:
    return send_mail(
        email,
        "Your Commissioner's Desk sign-in link",
        "Sign in to the Commissioner's Desk:\n\n"
        f"{url}\n\n"
        "This link works once and expires in 15 minutes. If you did not "
        "request it, ignore this message and nothing happens.",
    )
