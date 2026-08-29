"""Sync both leagues from Sleeper and print a status summary.

Usage: .venv/Scripts/python.exe scripts/sync.py [--weeks-back N]
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from leaguepage.ingest import sync_all
from leaguepage.storage import Storage


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--weeks-back", type=int, default=1)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    with Storage() as storage:
        results = sync_all(storage, weeks_back=args.weeks_back)
        week = storage.get_meta("current_week")
        season_type = storage.get_meta("season_type")

    print(f"\nFantasy week: {week} (NFL season_type: {season_type})")
    ok = True
    for r in results:
        status = "OK " if r.ok else "FAIL"
        print(
            f"[{status}] {r.league.slug:8s} rosters={r.rosters} users={r.users} "
            f"drafts={r.drafts} picks={r.picks} weeks={r.weeks_synced}"
        )
        for w in r.warnings:
            print(f"       warning: {w}")
        if r.error:
            print(f"       error: {r.error}")
            ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
