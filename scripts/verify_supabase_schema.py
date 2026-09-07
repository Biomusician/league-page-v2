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

# Which migration creates each table, so a "missing" line names the file
# to run rather than sending you to 0001 for something 0004 owns.
OWNER = {
    "app_commissioners": "0001", "issues": "0001", "issue_modules": "0001",
    "sections": "0001", "prose_revisions": "0001",
    "issue_revision_requests": "0001", "team_names": "0001",
    "story_decisions": "0001", "award_decisions": "0001",
    "matchup_state": "0001", "power_rankings": "0001", "takes": "0001",
    "editorial_usage": "0001", "bit_usage": "0001", "editorial_meta": "0001",
    "jobs": "0001", "job_events": "0004", "sync_snapshots": "0002",
}
TABLES = list(OWNER)


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
        print(f"NOT VISIBLE TO PostgREST: {len(missing)} of {len(TABLES)} "
              "table(s).")
        for t in missing:
            print(f"  {t:26s} created by migration {OWNER[t]}")
        print()
        # This script reads one transport. PGRST205 means PostgREST cannot
        # see the table, which is true both when the migration has not run
        # and when it has and the schema cache is stale -- and the cache
        # cannot be reloaded with NOTIFY through the connection pooler.
        print("  Two causes look identical from here:")
        print(f"    1. the migration has not been applied -> run "
              f"migrations/{sorted({OWNER[t] for t in missing})[0]}_*.sql")
        print("    2. it has, and PostgREST's schema cache is stale ->")
        print("       Dashboard -> Settings -> API -> Reload schema cache")
        print("  Tell them apart with a direct connection:")
        print("    select to_regclass('public.<table>')")
        print()
        print(f"  {c['url'].replace('.supabase.co', '')}"
              .replace("https://", "https://supabase.com/dashboard/project/")
              + "/sql/new")
        return 1
    print(f"SCHEMA OK: {len(locked)}/{len(TABLES)} tables present and locked "
          "against the anon key (RLS working).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
