"""Report what the current Supabase configuration can actually do.

    .venv/Scripts/python.exe scripts/check_supabase.py

Prints configuration presence (never values for secret-class names), probes
the project with the publishable key only, and states exactly which
capability is unlocked and which is still blocked. Makes no changes.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from leaguepage import settings, supabase_client  # noqa: E402


def main() -> int:
    loaded = settings.load_env()
    print(f"config source: .env ({loaded} names loaded)" if loaded
          else "config source: process environment only (.env absent or empty)")
    print()
    print("CONFIGURATION")
    for row in settings.describe():
        mark = "set " if row["set"] else "MISSING"
        tag = " [SECRET]" if row["secret"] else ""
        detail = f"  {row['hint']}" if row["hint"] and not row["secret"] else ""
        print(f"  {mark:7s} {row['name']:32s}{tag}{detail}")

    print()
    print("SUPABASE PROBE (publishable key only)")
    report = supabase_client.probe()
    if not report["configured"]:
        print("  not configured — nothing to probe.")
        print()
        print("NEXT STEP")
        print("  Create .env at the repo root (copy .env.example) and set:")
        print(f"    {settings.SUPABASE_URL}=https://<project-ref>.supabase.co")
        print(f"    {settings.SUPABASE_PUBLISHABLE_KEY}=<publishable key>")
        print("  Neither is secret-class; both stay out of git (.env is ignored).")
        return 1
    print(f"  reachable:      {report['reachable']}")
    print(f"  auth health:    {report['auth_health']}")
    print(f"  key accepted:   {report['key_accepted']}")
    for k in ("external_email_enabled", "mailer_autoconfirm", "disable_signup"):
        if k in report:
            print(f"  {k}: {report[k]}")
    if report["detail"]:
        print(f"  detail:         {report['detail']}")

    print()
    print("CAPABILITY")
    if report.get("key_accepted"):
        print("  UNLOCKED  email OTP sign-in (server-side; no key reaches the browser)")
    else:
        print("  BLOCKED   sign-in — the publishable key was not accepted")
    print("  MANUAL    schema: run migrations/0001_commissioner_state.sql in the")
    print("            Supabase SQL editor (needs no local credential)")
    print("  PENDING   editorial CRUD: needs the schema applied, then either the")
    print("            signed-in Commissioner's JWT (least privilege, preferred)")
    print(f"            or {settings.SUPABASE_SECRET_KEY} / "
          f"{settings.DATABASE_URL} for admin tasks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
