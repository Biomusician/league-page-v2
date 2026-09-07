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


def really_exists(tables: list[str]) -> dict[str, bool] | None:
    """Ask the database directly, when we have a connection to ask with.

    PGRST205 means only that PostgREST cannot see a table, which is true
    both when the migration has not run and when it has and the API's
    schema cache is stale. One `to_regclass` settles it. Returns None when
    there is no DSN, which is the normal case on a machine that only has
    the publishable key.
    """
    dsn = settings.get(settings.DATABASE_URL)
    if not dsn:
        return None
    try:
        import psycopg
    except ImportError:
        return None
    try:
        with psycopg.connect(dsn, connect_timeout=20) as conn:
            return {t: conn.execute("select to_regclass(%s)", (f"public.{t}",)
                                    ).fetchone()[0] is not None
                    for t in tables}
    except Exception as exc:                                # noqa: BLE001
        print(f"  (direct check unavailable: {type(exc).__name__})")
        return None


def expected_columns() -> dict[str, set[str]]:
    """What the migration files say each table should have."""
    import re

    out: dict[str, set[str]] = {}
    for path in sorted((REPO / "migrations").glob("*.sql")):
        sql = path.read_text(encoding="utf-8")
        for name, body in re.findall(
                r"create table if not exists\s+(\w+)\s*\((.*?)\n\);",
                sql, re.S | re.I):
            cols = set()
            for line in body.splitlines():
                line = line.strip()
                if not line or line.startswith("--"):
                    continue
                first = line.split()[0].strip(",").lower()
                if re.fullmatch(r"\w+", first) and first not in {
                        "primary", "unique", "foreign", "constraint", "check"}:
                    cols.add(first)
            out.setdefault(name, set()).update(cols)
        for name, col in re.findall(
                r"alter table\s+(?:if exists\s+)?(\w+)\s+add column\s+"
                r"(?:if not exists\s+)?(\w+)", sql, re.I):
            out.setdefault(name, set()).add(col)
        for name, old, new in re.findall(
                r"alter table\s+(?:if exists\s+)?(\w+)\s+rename column\s+"
                r"(\w+)\s+to\s+(\w+)", sql, re.I):
            out.setdefault(name, set()).discard(old)
            out[name].add(new)
        # A later migration may remove a column an earlier one created --
        # 0004 drops jobs.stages. Without this the verifier reports a
        # column as missing that is supposed to be missing.
        for name, col in re.findall(
                r"alter table\s+(?:if exists\s+)?(\w+)\s+drop column\s+"
                r"(?:if exists\s+)?(\w+)", sql, re.I):
            out.setdefault(name, set()).discard(col)
    return out


def missing_columns() -> dict[str, list[str]] | None:
    """Columns the migrations define that the live database does not have.

    This is the check that would have caught migration 0003: it adds
    twenty columns to a table that already existed, so nothing about the
    table's presence could reveal that it had not run.
    """
    dsn = settings.get(settings.DATABASE_URL)
    if not dsn:
        return None
    try:
        import psycopg
    except ImportError:
        return None
    want = expected_columns()
    try:
        with psycopg.connect(dsn, connect_timeout=20) as conn:
            gaps = {}
            for table, cols in sorted(want.items()):
                live = {r[0] for r in conn.execute(
                    "select column_name from information_schema.columns "
                    "where table_schema='public' and table_name=%s",
                    (table,)).fetchall()}
                if not live:
                    continue          # the table check above owns this case
                absent = sorted(cols - live)
                if absent:
                    gaps[table] = absent
            return gaps
    except Exception as exc:                                # noqa: BLE001
        print(f"  (column check unavailable: {type(exc).__name__})")
        return None


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
        truth = really_exists(missing)
        if truth is None:
            print("  Two causes look identical from here, and this machine "
                  "has no DATABASE_URL to settle it:")
            print(f"    1. the migration has not run -> apply "
                  f"migrations/{sorted({OWNER[t] for t in missing})[0]}_*.sql")
            print("    2. it has, and PostgREST's schema cache is stale ->")
            print("       Dashboard -> Settings -> API -> Reload schema cache")
            print(f"  {c['url'].replace('.supabase.co', '')}"
                  .replace("https://", "https://supabase.com/dashboard/project/")
                  + "/sql/new")
            return 1
        absent = sorted(t for t, there in truth.items() if not there)
        if absent:
            print("  DIRECT CHECK: genuinely absent from the database.")
            for t in absent:
                print(f"    {t:26s} apply migrations/{OWNER[t]}_*.sql")
            return 1
        print("  DIRECT CHECK: every one of them EXISTS in the database.")
        print("  So this is PostgREST's schema cache, not the schema. It is")
        print("  pinned to an older snapshot and a reload has not moved it.")
        print()
        print("  Impact today: NONE. No data flows over PostgREST -- the")
        print("  application talks to Postgres directly and supabase_client")
        print("  is authentication only. This affects this script and")
        print("  anything that might later read the database from a browser.")
        print()
        return report_columns()
    print(f"TABLES OK: {len(locked)}/{len(TABLES)} present and locked "
          "against the anon key (RLS working).")

    return report_columns()


def report_columns() -> int:
    """Print the column verdict. Called on every path that gets far enough
    to have one, because a stale API cache says nothing about columns."""
    gaps = missing_columns()
    if gaps is None:
        print("COLUMNS: not checked (no DATABASE_URL on this machine).")
        print("  Table presence alone cannot tell you a migration that only "
              "ADDS columns has run.")
        return 0
    if gaps:
        print(f"COLUMNS MISSING in {len(gaps)} table(s):")
        for table, cols in gaps.items():
            shown = ", ".join(cols[:6]) + (" ..." if len(cols) > 6 else "")
            print(f"  {table:26s} {len(cols):2d} absent: {shown}")
        print("  A migration that only adds columns leaves the table present,")
        print("  so this is the only check that can see it. Apply the "
              "migration that defines them.")
        return 1
    print("COLUMNS OK: every column the migrations define is present.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
