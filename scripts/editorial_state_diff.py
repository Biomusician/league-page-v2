"""What authoritative editorial state exists locally, and what is in the cloud.

Read-only. Prints a row count per table on both sides and, for the tables
whose shape allows it, how many rows are identical, local-only,
remote-only or different. Nothing is written by this script ever -- the
import that acts on it is a separate, explicit step.

    .venv/Scripts/python.exe scripts/editorial_state_diff.py

Cache and recomputable tables are deliberately absent: a hosted Desk
re-syncs Sleeper and re-indexes the archive, so copying 12,700 rows of
cache would buy nothing. The list here is exactly the state a
Commissioner authored.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from leaguepage import settings  # noqa: E402
from leaguepage.storage import Storage  # noqa: E402

# table -> the columns that identify a row, and the columns that carry
# meaning. Identity decides "same row"; payload decides "same content".
TABLES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "issues": (("league_slug", "season", "issue_key"), ("status", "theme")),
    "issue_modules": (("league_slug", "season", "issue_key", "module_key"),
                      ("position", "included", "custom_title", "approved",
                       "approved_sha")),
    "sections": (("league_slug", "season", "issue_key", "kind", "section"),
                 ("content", "state")),
    "prose_revisions": (("league_slug", "season", "issue_key", "section",
                         "source", "prior_text"), ()),
    "prose_provenance": (("league_slug", "season", "issue_key", "section"),
                         ("generator", "method", "generated_sha", "origin",
                          "assistance", "event")),
    "matchup_state": (("league_slug", "season", "week", "matchup_slug"),
                      ("selected_angle_id", "custom_angle", "angle_note",
                       "prominence_override", "status", "revision_requests",
                       "covered_sha")),
    "issue_revision_requests": (("league_slug", "season", "issue_key",
                                 "section", "note"), ("status",)),
    "takes": (("league_slug", "season", "text"), ("status",)),
    "story_decisions": (("league_slug", "season", "workflow", "candidate_id"),
                        ("decision", "note")),
    "award_decisions": (("league_slug", "season", "workflow", "award_key"),
                        ("winner", "note")),
    "power_rankings": (("league_slug", "season", "label", "roster_id"),
                       ("rank", "note")),
    "team_names": (("league_slug", "roster_id"), ("public_name",)),
    "force_flow_notes": (("league_slug", "season", "txn_id"), ("note",)),
    "editorial_meta": (("key",), ("value",)),
    "bit_usage": (("league_slug", "season"), ()),
    "editorial_usage": (("league_slug", "season"), ()),
    "sync_snapshots": (("league_slug", "season", "payload_hash"), ()),
}

# SQLite calls its meta table `meta`; Postgres calls it `editorial_meta`.
LOCAL_NAME = {"editorial_meta": "meta"}


def _rows(conn, table: str, cols: tuple[str, ...]) -> list[tuple] | None:
    """A cursor of its own, because sqlite3 connections and psycopg
    cursors do not share an interface and the caller should not care."""
    try:
        cur = conn.execute(f"SELECT {', '.join(cols)} FROM {table}")
        return [tuple(r) for r in cur.fetchall()]
    except Exception:                                       # noqa: BLE001
        return None


def main() -> int:
    dsn = settings.get(settings.DATABASE_URL)
    if not dsn:
        print("DATABASE_URL is not configured; nothing to compare against.")
        return 1
    import psycopg

    print(f"{'table':26s} {'local':>7s} {'cloud':>7s} {'same':>7s} "
          f"{'local-only':>11s} {'cloud-only':>11s} {'differs':>8s}")
    totals = {"local": 0, "cloud": 0, "local_only": 0, "cloud_only": 0,
              "differs": 0}
    with Storage() as s, psycopg.connect(dsn, connect_timeout=20) as pg:
        for table, (identity, payload) in TABLES.items():
            cols = identity + payload
            local = _rows(s._conn, LOCAL_NAME.get(table, table), cols)  # noqa: SLF001
            remote = _rows(pg, table, cols)
            if local is None or remote is None:
                where = "local" if local is None else "cloud"
                print(f"{table:26s} {'-':>7s} {'-':>7s}   (not comparable: "
                      f"{where} shape differs)")
                continue
            n = len(identity)
            lmap = {r[:n]: r[n:] for r in local}
            rmap = {r[:n]: r[n:] for r in remote}
            same = sum(1 for k in lmap.keys() & rmap.keys() if lmap[k] == rmap[k])
            differs = len(lmap.keys() & rmap.keys()) - same
            local_only = len(lmap.keys() - rmap.keys())
            cloud_only = len(rmap.keys() - lmap.keys())
            print(f"{table:26s} {len(local):7d} {len(remote):7d} {same:7d} "
                  f"{local_only:11d} {cloud_only:11d} {differs:8d}")
            totals["local"] += len(local)
            totals["cloud"] += len(remote)
            totals["local_only"] += local_only
            totals["cloud_only"] += cloud_only
            totals["differs"] += differs
    print(f"\n{'TOTAL':26s} {totals['local']:7d} {totals['cloud']:7d} "
          f"{'':7s} {totals['local_only']:11d} {totals['cloud_only']:11d} "
          f"{totals['differs']:8d}")
    print("\nRead-only. Nothing was written.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
