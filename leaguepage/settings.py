"""Private runtime configuration, loaded from the environment.

One place reads configuration; nothing else touches os.environ for these
values and no template or source file ever embeds them. A local `.env` at
the repo root is loaded if present (gitignored; see .env.example for the
variable names). Real values live only there and, later, in the PRIVATE
Commissioner Vercel project's environment.

Sensitivity classes, because they are not equal:

  SUPABASE_URL              not secret (a public project endpoint)
  SUPABASE_PUBLISHABLE_KEY  not secret by design (Supabase's browser-safe
                            key). This app still keeps it server-side: the
                            OTP flow runs on the server, so the browser is
                            never handed any key at all.
  SUPABASE_SECRET_KEY       SECRET. Server only. Bypasses RLS.
  DATABASE_URL              SECRET. Contains the Postgres password.
  LEAGUEPAGE_SECRET_KEY     SECRET. Signs session cookies.

Never log, render, commit, or send a SECRET-class value anywhere.
"""
from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = REPO_ROOT / ".env"

# Names only. Values are never defaulted to anything real.
SUPABASE_URL = "SUPABASE_URL"
SUPABASE_PUBLISHABLE_KEY = "SUPABASE_PUBLISHABLE_KEY"
SUPABASE_SECRET_KEY = "SUPABASE_SECRET_KEY"
DATABASE_URL = "DATABASE_URL"

SECRET_CLASS = frozenset({SUPABASE_SECRET_KEY, DATABASE_URL,
                          "LEAGUEPAGE_SECRET_KEY", "RESEND_API_KEY"})

_loaded = False


def load_env(path: Path | None = None, *, force: bool = False) -> int:
    """Read KEY=VALUE lines from .env into os.environ without overwriting
    anything already set (real environment wins over the file). Returns the
    number of names loaded. Deliberately tiny: a dependency for this would
    not save real work."""
    global _loaded
    if _loaded and not force:
        return 0
    f = path or ENV_FILE
    count = 0
    if f.exists():
        for raw in f.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and value and key not in os.environ:
                os.environ[key] = value
                count += 1
    _loaded = True
    return count


def get(name: str, default: str | None = None) -> str | None:
    load_env()
    value = os.environ.get(name, default)
    return value.strip() if isinstance(value, str) else value


def present(name: str) -> bool:
    return bool(get(name))


def describe() -> list[dict]:
    """Configuration status for diagnostics. Reports only WHETHER a value is
    set, plus a non-reversible hint for non-secret values so a typo is
    visible. SECRET-class values never reveal any characters."""
    out = []
    for name in (SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY, SUPABASE_SECRET_KEY,
                 DATABASE_URL, "LEAGUEPAGE_AUTH_MODE",
                 "LEAGUEPAGE_COMMISSIONER_EMAILS", "LEAGUEPAGE_SECRET_KEY"):
        value = get(name)
        secret = name in SECRET_CLASS
        hint = ""
        if value and not secret:
            hint = value if name == SUPABASE_URL else f"{value[:6]}…({len(value)} chars)"
        elif value and secret:
            hint = "set"
        out.append({"name": name, "set": bool(value), "secret": secret,
                    "hint": hint})
    return out
