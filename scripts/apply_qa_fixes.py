"""Apply the publication gate's mechanical copy fixes as a correction.

Scope, deliberately narrow: only findings in the COPY category that carry an
exact fix_from/fix_to pair — a doubled period, a repeated word, a comma
doing a full stop's job, "way to many". Those are typo corrections, which is
what a copy desk does. Identity, freshness and analytical findings need the
Commissioner's judgment about what to call people and what a number means,
and this script will not touch them.

    scripts/apply_qa_fixes.py --league disco                 # show the diff
    scripts/apply_qa_fixes.py --league disco --apply         # correct it

--apply does two things: rewrites the editorial source so the typo cannot
come back on a future republish, and publishes a CORRECTION revision beside
the original snapshot (never over it). The original stays on disk and in git
as the record of what actually shipped that day.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from leaguepage import pubqa
from leaguepage.config import EDITORIAL_DIR, LEAGUES, PUBLISHED_DIR, get_league
from leaguepage.publish import PublishError, revise_issue
from leaguepage.storage import Storage

DEFAULT_NOTE = "copy corrections: punctuation and typography"


def _source_path(league_slug: str, season: str, issue_key: str,
                 module_key: str) -> Path:
    base = EDITORIAL_DIR / season / league_slug / issue_key
    if module_key == "lowdown":
        return base / "lowdown" / "lowdown.md"
    return base / "sections" / f"{module_key}.md"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--league", required=True)
    ap.add_argument("--issue", default="draft")
    ap.add_argument("--apply", action="store_true",
                    help="write the correction; without it, only show the diff")
    ap.add_argument("--note", default=DEFAULT_NOTE)
    args = ap.parse_args()

    league = get_league(args.league)
    snap_path = None
    with Storage() as s:
        season = str((s.get_league(league.league_id) or {}).get("season") or "")
        snap_path = PUBLISHED_DIR / league.slug / season / f"{args.issue}.json"
        if not snap_path.exists():
            print(f"no published snapshot at {snap_path}")
            return 1
        snapshot = json.loads(snap_path.read_text(encoding="utf-8"))
        ctx = pubqa.build_context(s, league, season, args.issue)
        findings = pubqa.check_sections(snapshot["sections"], ctx, published=True)

    fixes = [f for f in findings
             if f.category == pubqa.COPY and f.fix_from and f.fix_to]
    if not fixes:
        print("no mechanical copy fixes available.")
        return 0

    sections = [dict(sec) for sec in snapshot["sections"]]
    by_key = {sec["module_key"]: sec for sec in sections}
    applied = []
    for f in fixes:
        sec = by_key.get(f.module_key)
        if sec is None:
            continue
        text = sec["content_md"]
        if text.count(f.fix_from) != 1:
            print(f"SKIP (ambiguous or missing): {f.title} in {f.module_key}")
            continue
        sec["content_md"] = text.replace(f.fix_from, f.fix_to, 1)
        applied.append(f)

    print(f"\n{league.slug} {season} {args.issue} — "
          f"{len(applied)} mechanical fix(es):\n")
    for f in applied:
        print(f"  {f.title}  ({f.module_key})")
        print(f"    -  {f.fix_from}")
        print(f"    +  {f.fix_to}\n")

    if not args.apply:
        print("dry run. Re-run with --apply to write the correction.")
        return 0
    if not applied:
        return 0

    # Keep the editorial source in step, so a later republish from the
    # workspace does not reintroduce a typo the correction removed.
    for f in applied:
        path = _source_path(league.slug, season, args.issue, f.module_key)
        if path.exists():
            text = path.read_text(encoding="utf-8")
            if text.count(f.fix_from) == 1:
                path.write_text(text.replace(f.fix_from, f.fix_to, 1),
                                encoding="utf-8")
                print(f"updated source {path}")

    with Storage() as s:
        try:
            out = revise_issue(s, league, season, args.issue, note=args.note,
                               sections=sections)
        except PublishError as exc:
            print(f"\nCorrection refused: {exc}")
            return 1
    print(f"\ncorrection written: {out}")
    print(f"original untouched: {snap_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
