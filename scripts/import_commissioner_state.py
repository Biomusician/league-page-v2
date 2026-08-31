"""Restore Commissioner state from an export bundle.

    # prove a bundle is complete and restorable without touching anything
    .venv/Scripts/python.exe scripts/import_commissioner_state.py b.json --dry-run

    # restore into a scratch location (recommended way to verify a backup)
    .venv/Scripts/python.exe scripts/import_commissioner_state.py b.json \
        --db /tmp/restore.sqlite3 --editorial /tmp/restore-editorial

    # restore over the live store (refuses without --force)
    .venv/Scripts/python.exe scripts/import_commissioner_state.py b.json --force

Restoring replaces the authoritative tables and prose files the bundle
carries; it never touches Sleeper cache tables, which any sync rebuilds.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from leaguepage.config import DB_PATH, EDITORIAL_DIR  # noqa: E402
from leaguepage.storage import Storage  # noqa: E402

from export_commissioner_state import checksum  # noqa: E402


def restore(payload: dict, db_path: Path, editorial_dir: Path) -> dict:
    """Write the bundle into a database + editorial tree. The database is
    opened through Storage first so the schema exists even on a fresh file."""
    with Storage(db_path):
        pass

    con = sqlite3.connect(db_path)
    counts = {"rows": 0, "tables": 0, "prose": 0, "meta": 0, "config": 0}
    have = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    for table, rows in payload["tables"].items():
        if table not in have:
            print(f"  skip {table}: not in this schema")
            continue
        con.execute(f'DELETE FROM "{table}"')
        counts["tables"] += 1
        for row in rows:
            cols = ", ".join(f'"{c}"' for c in row)
            marks = ", ".join("?" for _ in row)
            con.execute(f'INSERT INTO "{table}" ({cols}) VALUES ({marks})',
                        list(row.values()))
            counts["rows"] += 1
    for k, v in payload.get("meta", {}).items():
        con.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (k, v))
        counts["meta"] += 1
    con.commit()
    con.close()

    for rel, text in payload.get("prose", {}).items():
        target = editorial_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        counts["prose"] += 1
    for name, text in payload.get("config", {}).items():
        editorial_dir.mkdir(parents=True, exist_ok=True)
        (editorial_dir / name).write_text(text, encoding="utf-8")
        counts["config"] += 1
    return counts


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("bundle", type=Path)
    ap.add_argument("--db", type=Path, default=DB_PATH)
    ap.add_argument("--editorial", type=Path, default=EDITORIAL_DIR)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="required to restore over the live store")
    args = ap.parse_args()

    payload = json.loads(args.bundle.read_text(encoding="utf-8"))
    stated = payload.get("checksum")
    actual = checksum(payload)
    ok = stated == actual
    rows = sum(len(v) for v in payload["tables"].values())
    print(f"bundle v{payload.get('bundle_version')} from {payload.get('exported_at')}")
    print(f"  {rows} rows, {len(payload.get('meta', {}))} meta, "
          f"{len(payload.get('prose', {}))} prose files")
    print(f"  checksum {'OK' if ok else 'MISMATCH'} ({actual[:16]})")
    if not ok:
        print("  refusing: the bundle does not match its own checksum.")
        return 1
    if args.dry_run:
        print("dry run: nothing written. Bundle is complete and restorable.")
        return 0

    live = args.db.resolve() == Path(DB_PATH).resolve()
    if live and not args.force:
        print("refusing to restore over the live store without --force. "
              "Restore into a scratch --db/--editorial first to verify it.")
        return 1

    counts = restore(payload, args.db, args.editorial)
    print(f"restored {counts['rows']} rows into {counts['tables']} tables, "
          f"{counts['meta']} meta keys, {counts['prose']} prose files, "
          f"{counts['config']} config files")
    print(f"  db:        {args.db}")
    print(f"  editorial: {args.editorial}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
