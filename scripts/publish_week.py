"""Render the public Common Tactical Picture page for a week.

Usage: .venv/Scripts/python.exe scripts/publish_week.py --league surfeit --week 1

Only APPROVED or LOCKED matchup drafts render; everything else shows
"Preview pending."
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from leaguepage.config import get_league
from leaguepage.publish import render_week
from leaguepage.storage import Storage


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--league", required=True)
    parser.add_argument("--week", type=int, required=True)
    args = parser.parse_args()
    with Storage() as storage:
        out = render_week(storage, get_league(args.league), args.week)
    if out is None:
        print("No matchup data for that week.")
        return 1
    print(f"rendered: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
