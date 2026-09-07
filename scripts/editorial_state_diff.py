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

TWO THINGS THIS SCRIPT LEARNED THE HARD WAY

Prose is not a local table. SQLite has no `sections`; the filesystem
backend keeps the words in files and only their history in the database.
So the local side of `sections` is read through the prose repository,
which is the only thing that knows where the words actually are.

And a failed statement poisons a Postgres transaction, so one wrong
column name used to make every table after it report "shape differs"
when the shape was fine. Each side now runs on its own autocommitting
connection and every failure prints the reason instead of a shrug.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from leaguepage import prose_store, settings  # noqa: E402
from leaguepage.config import EDITORIAL_DIR, LEAGUES  # noqa: E402
from leaguepage.storage import Storage  # noqa: E402

# table -> the columns that identify a row, and the columns that carry
# meaning. Identity decides "same row"; payload decides "same content".
TABLES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "issues": (("league_slug", "season", "issue_key"), ("status", "theme")),
    "issue_modules": (("league_slug", "season", "issue_key", "module_key"),
                      ("position", "included", "custom_title", "approved",
                       "approved_sha")),
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
    "takes": (("league_slug", "season", "quote"), ("status", "public")),
    "story_decisions": (("league_slug", "season", "workflow", "candidate_id"),
                        ("decision", "note", "route")),
    "award_decisions": (("league_slug", "season", "workflow", "award_key"),
                        ("decision", "winner", "note")),
    "power_rankings": (("league_slug", "season", "label", "roster_id"),
                       ("rank", "tier", "note")),
    "team_names": (("league_slug", "roster_id"), ("public_name",)),
    "force_flow_notes": (("league_slug", "season", "txn_id"), ("note",)),
    "editorial_usage": (("league_slug", "season", "week", "matchup_slug",
                         "kind", "value"), ()),
    "bit_usage": (("manager_key", "bit", "league_slug", "season", "week"), ()),
}

# `rank` is a window function to a Postgres parser and a plain column to
# SQLite, so it has to be quoted differently on the two sides.
QUOTED = {"rank"}

# Deliberately not compared, with the reason, so that "absent from this
# table" never has to mean "nobody thought about it".
NOT_COMPARED = {
    "sync_snapshots": "the inbox baseline. A hosted Desk syncs Sleeper "
                      "itself; copying snapshot payloads would move a "
                      "cache, and `reviewed_at` describes a queue that no "
                      "longer exists after a resync",
    "meta / editorial_meta": "recomputable settings, and the two stores "
                             "do not even agree on the table's name",
    "sleeper cache": "12,700 rows a resync rebuilds",
}


def _sql(cols: tuple[str, ...], quote: str) -> str:
    return ", ".join(f"{quote}{c}{quote}" if c in QUOTED else c for c in cols)


def _local(conn, table: str, cols: tuple[str, ...]):
    try:
        return [tuple(r) for r in conn.execute(
            f'SELECT {_sql(cols, chr(34))} FROM {table}').fetchall()], None
    except Exception as exc:                                # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"


def _remote(conn, table: str, cols: tuple[str, ...]):
    try:
        with conn.transaction(force_rollback=True):
            cur = conn.execute(f'SELECT {_sql(cols, chr(34))} FROM {table}')
            return [tuple(r) for r in cur.fetchall()], None
    except Exception as exc:                                # noqa: BLE001
        return None, f"{type(exc).__name__}: {str(exc).splitlines()[0]}"


def local_prose() -> list[tuple]:
    """Every stored prose object, read the way the application reads it.

    SQLite has no `sections` table: on the filesystem backend the words
    are files and the database holds only their history. So this asks the
    repository, which is the one thing that knows.
    """
    repo = prose_store.FilesystemProseRepository(base_dir=EDITORIAL_DIR)
    out = []
    for season_dir in sorted(EDITORIAL_DIR.glob("[0-9][0-9][0-9][0-9]")):
        for league in LEAGUES:
            parent = season_dir / league.slug
            if not parent.is_dir():
                continue
            for idir in sorted(d for d in parent.iterdir() if d.is_dir()):
                for rec in repo.list_issue(league.slug, season_dir.name,
                                           idir.name):
                    if rec.exists:
                        out.append((rec.key.league, rec.key.season,
                                    rec.key.issue, rec.key.kind,
                                    rec.key.name, rec.text))
    return out


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
    problems: list[str] = []
    with Storage() as s, psycopg.connect(dsn, connect_timeout=20,
                                         autocommit=True) as pg:
        for table, (identity, payload) in TABLES.items():
            cols = identity + payload
            local, lerr = _local(s._conn, table, cols)          # noqa: SLF001
            remote, rerr = _remote(pg, table, cols)
            if lerr or rerr:
                where = "local" if lerr else "cloud"
                print(f"{table:26s} {'-':>7s} {'-':>7s}   NOT COMPARED "
                      f"({where}: {lerr or rerr})")
                problems.append(f"{table}: {lerr or rerr}")
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

        # Prose, which is not a local table at all.
        try:
            words = local_prose()
        except Exception as exc:                            # noqa: BLE001
            words = []
            problems.append(f"sections (local prose): {type(exc).__name__}: {exc}")
        remote, rerr = _remote(pg, "sections",
                               ("league_slug", "season", "issue_key", "kind",
                                "section", "content"))
        if rerr:
            print(f"{'sections':26s} {len(words):7d} {'-':>7s}   NOT COMPARED "
                  f"(cloud: {rerr})")
            problems.append(f"sections: {rerr}")
        else:
            lmap = {w[:5]: w[5:] for w in words}
            rmap = {r[:5]: r[5:] for r in remote}
            same = sum(1 for k in lmap.keys() & rmap.keys() if lmap[k] == rmap[k])
            print(f"{'sections (prose)':26s} {len(words):7d} {len(remote):7d} "
                  f"{same:7d} {len(lmap.keys() - rmap.keys()):11d} "
                  f"{len(rmap.keys() - lmap.keys()):11d} "
                  f"{len(lmap.keys() & rmap.keys()) - same:8d}")
            totals["local"] += len(words)
            totals["cloud"] += len(remote)
            totals["local_only"] += len(lmap.keys() - rmap.keys())
            totals["cloud_only"] += len(rmap.keys() - lmap.keys())
            totals["differs"] += len(lmap.keys() & rmap.keys()) - same

    print(f"\n{'TOTAL':26s} {totals['local']:7d} {totals['cloud']:7d} "
          f"{'':7s} {totals['local_only']:11d} {totals['cloud_only']:11d} "
          f"{totals['differs']:8d}")
    print("\nDeliberately not compared:")
    for what, why in NOT_COMPARED.items():
        print(f"  {what}: {why}")
    if problems:
        print(f"\n{len(problems)} TABLE(S) COULD NOT BE COMPARED:")
        for p in problems:
            print(f"  {p}")
        print("  A table nobody can see is a table nobody can import. Fix "
              "these before treating this run as a dry run.")
    print("\nRead-only. Nothing was written.")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
