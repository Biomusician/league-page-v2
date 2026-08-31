"""Emit the SQL that seeds the Commissioner allowlist table, and put it on
the clipboard.

WHY THIS EXISTS AS A SEPARATE STEP

Migration 0001 enables and *forces* Row Level Security on every table,
including app_commissioners itself, and the only policy grants access to a
user already listed in app_commissioners. That is the correct end state, but
it cannot bootstrap itself: while the table is empty, nobody satisfies the
policy, so nobody can insert the first row. The publishable key is the anon
role and is granted nothing at all, so the application cannot do this either
— by design, since an application that could add itself to its own allowlist
would not be an allowlist.

Breaking the cycle therefore requires one statement run with database owner
rights, which in practice means the Supabase SQL editor. This script writes
that statement so the address is generated from local configuration rather
than typed by hand, and so it is never committed: the email lives only in
the gitignored .env.

Safe to run repeatedly; the SQL is idempotent.

    .venv\\Scripts\\python.exe scripts/make_commissioner_seed.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from leaguepage import auth


def build_sql(emails: list[str]) -> str:
    values = ",\n  ".join(
        f"(lower('{e}'), 'seeded from LEAGUEPAGE_COMMISSIONER_EMAILS')"
        for e in emails)
    return (
        "insert into app_commissioners (email, note) values\n"
        f"  {values}\n"
        "on conflict (email) do nothing;\n")


def to_clipboard(text: str) -> bool:
    try:
        subprocess.run(["clip"], input=text.encode("utf-16-le"),
                       check=True, shell=True)
        return True
    except Exception:
        return False


def main() -> int:
    emails = sorted(auth.allowlist())
    if not emails:
        print("LEAGUEPAGE_COMMISSIONER_EMAILS is not set in .env — nothing "
              "to seed.")
        return 1

    sql = build_sql(emails)
    print(f"Allowlist ({len(emails)}): {', '.join(emails)}")
    print()
    print(sql)

    if to_clipboard(sql):
        print("Copied to clipboard.")
    else:
        print("Clipboard unavailable; copy the SQL above by hand.")
    print()
    print("Run it in the Supabase SQL editor:")
    print("  Dashboard -> SQL Editor -> New query -> paste -> Run")
    print()
    print("This is deliberately not automatable: the anon key is granted "
          "nothing,\nand no application should be able to write its own "
          "allowlist.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
