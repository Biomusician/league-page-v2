"""Seed the Commissioner allowlist table.

WHY THIS EXISTS AS A SEPARATE STEP

Migration 0001 enables and *forces* Row Level Security on every table,
including app_commissioners itself, and the only policy grants access to a
user already listed in app_commissioners. That is the correct end state, but
it cannot bootstrap itself: while the table is empty, nobody satisfies the
policy, so nobody can insert the first row. The publishable key is the anon
role and is granted nothing at all, so the application cannot do this either
— by design, since an application that could add itself to its own allowlist
would not be an allowlist.

Breaking the cycle needs one statement run with database owner rights. Two
routes, and the script takes whichever is available:

  DATABASE_URL set   connect as the owner and run it directly, then read the
                     table back to prove it landed. Nothing to paste.
  otherwise          print the statement and put it on the clipboard for the
                     Supabase SQL editor.

DATABASE_URL is SECRET-class and is read only from the gitignored `.env`.
It is used by this migration tooling and nothing else: the application talks
to Supabase over PostgREST with the signed-in Commissioner's token, so no
database password is needed at runtime, in the browser, or in any hosting
environment.

Safe to run repeatedly; the insert is idempotent.

    .venv\\Scripts\\python.exe scripts/make_commissioner_seed.py
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from leaguepage import auth, settings

NOTE = "seeded from LEAGUEPAGE_COMMISSIONER_EMAILS"
# Deliberately strict: these addresses are interpolated into SQL text for the
# clipboard route, where no parameter binding is available.
EMAIL_OK = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


def build_sql(emails: list[str]) -> str:
    values = ",\n  ".join(f"(lower('{e}'), '{NOTE}')" for e in emails)
    return ("insert into app_commissioners (email, note) values\n"
            f"  {values}\n"
            "on conflict (email) do nothing;\n")


def to_clipboard(text: str) -> bool:
    """clip.exe is not always reachable (it fails with Access is denied under
    some shells), so fall back to PowerShell before giving up."""
    try:
        subprocess.run(["clip"], input=text.encode("utf-16-le"),
                       check=True, shell=True, capture_output=True)
        return True
    except Exception:
        pass
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", "Set-Clipboard"],
            input=text.encode("utf-8"), check=True, capture_output=True)
        return True
    except Exception:
        return False


def apply_directly(dsn: str, emails: list[str]) -> list[str]:
    """Insert with bound parameters and return the resulting allowlist."""
    import psycopg

    with psycopg.connect(dsn, connect_timeout=20) as conn:
        with conn.cursor() as cur:
            cur.executemany(
                "insert into app_commissioners (email, note) "
                "values (lower(%s), %s) on conflict (email) do nothing",
                [(e, NOTE) for e in emails])
            conn.commit()
            cur.execute("select email from app_commissioners order by email")
            return [r[0] for r in cur.fetchall()]


def main() -> int:
    emails = sorted(auth.allowlist())
    if not emails:
        print("LEAGUEPAGE_COMMISSIONER_EMAILS is not set in .env — nothing "
              "to seed.")
        return 1
    bad = [e for e in emails if not EMAIL_OK.match(e)]
    if bad:
        print(f"refusing to seed malformed address(es): {', '.join(bad)}")
        return 1

    print(f"Allowlist ({len(emails)}): {', '.join(emails)}")
    print()

    dsn = settings.get(settings.DATABASE_URL)
    if dsn:
        print("DATABASE_URL is set; applying directly.")
        try:
            rows = apply_directly(dsn, emails)
        except Exception as exc:
            # never echo the DSN: it carries the password
            print(f"FAILED to apply: {type(exc).__name__}: "
                  f"{str(exc)[:200]}")
            return 1
        print(f"app_commissioners now holds {len(rows)} row(s): "
              f"{', '.join(rows)}")
        missing = [e for e in emails if e not in rows]
        if missing:
            print(f"WARNING: still missing {', '.join(missing)}")
            return 1
        print("Seeded and verified.")
        return 0

    sql = build_sql(emails)
    print(sql)
    # always leave a file behind: the clipboard is not always available, and
    # this address must not be retyped by hand
    out = Path(__file__).resolve().parent.parent / "backups" / "seed_commissioner.sql"
    out.parent.mkdir(exist_ok=True)
    out.write_text(sql, encoding="utf-8")
    if to_clipboard(sql):
        print("Copied to clipboard.")
    else:
        print("Clipboard unavailable.")
    print(f"Also written to: {out}")
    print()
    print("Run it in the Supabase SQL editor:")
    print("  Dashboard -> SQL Editor -> New query -> paste -> Run")
    print()
    print("Alternatively, put the Postgres connection string in .env as")
    print("DATABASE_URL and re-run this script; it will then apply and")
    print("verify the seed with no pasting. Do not put that value anywhere")
    print("but .env.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
