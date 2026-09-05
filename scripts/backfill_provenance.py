"""Retroactive provenance, only where the record proves it.

Usage: .venv/Scripts/python.exe scripts/backfill_provenance.py [--apply]

The one structural proof of AI origin the older workflow left behind is a
saved prior text carrying the ROUGH DRAFT marker: that marker is written
under the Claude Code authoring contract and by nothing else. Where the
earliest saved revision of a section carries it and no provenance row
exists, the section is recorded as AI in origin with that text as the
generated baseline. Everything else is left alone: a section whose first
saved text has no marker has no known author, and "sounds like Claude" is
not evidence.

Published snapshots are never touched. Dry run by default.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from leaguepage import provenance  # noqa: E402
from leaguepage.config import DB_PATH  # noqa: E402
from leaguepage.matchup_packet import ROUGH_DRAFT_MARKER  # noqa: E402
from leaguepage.storage import Storage  # noqa: E402


def candidates(storage: Storage) -> list[dict]:
    """Earliest saved prior text per section that carries the marker and
    has no provenance row. Returned rows never include the prose."""
    rows = storage._conn.execute(
        "SELECT league_slug, season, issue_key, section, source, prior_text, created_at "
        "FROM prose_revisions ORDER BY id").fetchall()
    first: dict[tuple, dict] = {}
    for r in rows:
        key = (r["league_slug"], r["season"], r["issue_key"], r["section"])
        first.setdefault(key, dict(r))
    out = []
    for key, r in first.items():
        if ROUGH_DRAFT_MARKER not in r["prior_text"]:
            continue
        if storage.get_prose_provenance(*key):
            continue
        out.append(r)
    return out


def backfill(storage: Storage, *, apply: bool) -> list[str]:
    lines = []
    for r in candidates(storage):
        section = r["section"]
        method = "matchup-brief" if section.startswith("matchup:") else "section-brief"
        lines.append(f"{r['league_slug']} {r['season']} {r['issue_key']} {section}: "
                     f"AI origin from {r['source']} revision of {r['created_at'][:10]} "
                     f"({len(r['prior_text'].split())} words)")
        if apply:
            provenance.record(storage, league_slug=r["league_slug"], season=r["season"],
                              issue_key=r["issue_key"], section=section,
                              generator="claude-code", method=method,
                              text=r["prior_text"], event="backfill")
    return lines


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true", help="write the rows (default: dry run)")
    ap.add_argument("--db", default=str(DB_PATH))
    args = ap.parse_args(argv)
    with Storage(Path(args.db)) as s:
        lines = backfill(s, apply=args.apply)
    for line in lines:
        print(("recorded  " if args.apply else "would record  ") + line)
    print(f"{len(lines)} section(s) {'recorded' if args.apply else 'eligible'}; "
          "everything else left unlabelled")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
