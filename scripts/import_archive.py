"""Index archive/*.md newsletters into the database (idempotent).

Usage: .venv/Scripts/python.exe scripts/import_archive.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from leaguepage.archive import index_archive
from leaguepage.storage import Storage


def main() -> int:
    with Storage() as storage:
        indexed = index_archive(storage)
        total = storage.archive_count()
    for item in indexed:
        season = item["season"] or "????"
        week = f"wk{item['week']}" if item["week"] else "---"
        print(f"{item['league']:8s} {season} {week:5s} {item['title']}")
    print(f"\nIndexed {len(indexed)} files; {total} issues in archive.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
