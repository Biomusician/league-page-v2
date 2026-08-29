"""Spot-check report for archive season dating.

Flags issues where the season inference deserves a human look:
  - the title contains a 4-digit year that differs from the inferred season
    (expected for the 2022+ ending-year titles, but listed so it's visible),
  - dating confidence below 'high',
  - no provenance recorded at all.

Usage: .venv/Scripts/python.exe scripts/audit_archive_dating.py [--all]
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from leaguepage.storage import Storage

_YEAR_RE = re.compile(r"\b(20\d\d)\b")


def main() -> int:
    show_all = "--all" in sys.argv
    with Storage() as storage:
        issues = storage.list_archive_issues()

    flagged = 0
    print(f"{'league':7s} {'season':6s} {'wk':>3s}  {'conf':6s} title / note")
    print("-" * 100)
    for it in issues:
        title_year = None
        m = _YEAR_RE.search(it["title"] or "")
        if m:
            title_year = m.group(1)
        conf = it["dating_confidence"] or "NONE"
        mismatch = title_year and it["season"] and title_year != it["season"]
        needs_look = mismatch or conf != "high"
        if not (needs_look or show_all):
            continue
        flagged += 1 if needs_look else 0
        wk = str(it["week"]) if it["week"] is not None else "-"
        line = f"{it['league_slug']:7s} {it['season'] or '----':6s} {wk:>3s}  {conf:6s} {it['title']}"
        if mismatch:
            line += f"  [title year {title_year} != season {it['season']}]"
        if it["dating_note"]:
            line += f"\n{'':27s}note: {it['dating_note']}"
        print(line)
    print("-" * 100)
    print(f"{flagged} of {len(issues)} issues flagged for spot-check "
          f"(title-year convention mismatches are expected for 2022+ titles).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
