"""Verify migration 0001 landed and that RLS actually locks anon out.

Runs with the publishable key only — no privileged credential needed — by
asking PostgREST for each table and reading the failure mode:

  PGRST205 / 404  -> table does not exist yet (migration not applied)
  42501           -> table exists and anon is denied  <- what we want
  200 with rows   -> DANGER: anon can read private editorial state

    .venv/Scripts/python.exe scripts/verify_supabase_schema.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from leaguepage import settings, supabase_client  # noqa: E402

TABLES = ["app_commissioners", "issues", "issue_modules", "sections",
          "prose_revisions", "issue_revision_requests", "team_names",
          "story_decisions", "award_decisions", "matchup_state",
          "power_rankings", "takes", "editorial_usage", "bit_usage",
          "editorial_meta", "jobs", "sync_snapshots"]


def classify(status: int, body: str) -> str:
    if status == 200:
        return "EXPOSED"
    if "42501" in body or status in (401, 403):
        return "locked"
    if "PGRST205" in body or status == 404:
        return "missing"
    return f"unknown({status})"


def main() -> int:
    c = supabase_client.config()
    if not supabase_client.configured():
        print("Supabase not configured; run scripts/check_supabase.py first.")
        return 1
    headers = {"apikey": c["key"], "Authorization": f"Bearer {c['key']}"}
    results = {}
    with httpx.Client(timeout=20) as client:
        for t in TABLES:
            try:
                r = client.get(f"{c['url']}/rest/v1/{t}",
                               headers=headers, params={"select": "*", "limit": 1})
                results[t] = classify(r.status_code, r.text)
            except Exception as exc:
                results[t] = f"error({type(exc).__name__})"

    missing = [t for t, v in results.items() if v == "missing"]
    exposed = [t for t, v in results.items() if v == "EXPOSED"]
    locked = [t for t, v in results.items() if v == "locked"]

    for t in TABLES:
        mark = {"locked": "OK  ", "missing": "--  ", "EXPOSED": "LEAK"}.get(
            results[t], "??  ")
        print(f"  {mark} {t:26s} {results[t]}")
    print()
    if exposed:
        print(f"FAIL: {len(exposed)} table(s) readable by the anon key: "
              f"{', '.join(exposed)}")
        return 2
    if missing:
        print(f"MIGRATION NOT APPLIED: {len(missing)} of {len(TABLES)} tables "
              "are missing.")
        print("  Apply migrations/0001_commissioner_state.sql in the SQL editor:")
        print(f"  {c['url'].replace('.supabase.co', '')}"
              .replace("https://", "https://supabase.com/dashboard/project/")
              + "/sql/new")
        return 1
    print(f"SCHEMA OK: {len(locked)}/{len(TABLES)} tables present and locked "
          "against the anon key (RLS working).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
