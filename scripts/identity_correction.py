"""Publish an identity correction to a published issue.

Separate from apply_qa_fixes.py on purpose. That script is COPY-only and a
test enforces it, because a typo has one correct form and a name does not:
deciding that "Jesse" and "Swanson" are the same roster is a factual claim
that has to be established from authoritative data and recorded, not
pattern-matched.

So every replacement here is passed in explicitly as `old||new`, each must
occur exactly once in the named section, and the diff is printed before
anything is written. The evidence for each mapping belongs in the commit
message and docs/DECISIONS.md, not in a regex.

    scripts/identity_correction.py --league surfeit \
        --replace 'lowdown||Jesse (team name pending)||Jesse (Swanson)'

Format: SECTION||OLD||NEW. Newlines in OLD may be written as \\n.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from leaguepage.config import EDITORIAL_DIR, PUBLISHED_DIR, get_league
from leaguepage.publish import PublishError, revise_issue, snapshot_family
from leaguepage.storage import Storage


def _source_path(league_slug: str, season: str, issue_key: str, module_key: str) -> Path:
    base = EDITORIAL_DIR / season / league_slug / issue_key
    if module_key == "lowdown":
        return base / "lowdown" / "lowdown.md"
    return base / "sections" / f"{module_key}.md"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--league", required=True)
    ap.add_argument("--issue", default="draft")
    ap.add_argument("--replace", action="append", required=True,
                    help="SECTION||OLD||NEW")
    ap.add_argument("--note", required=True)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    league = get_league(args.league)
    with Storage() as s:
        season = str((s.get_league(league.league_id) or {}).get("season") or "")
    family = snapshot_family(PUBLISHED_DIR, league.slug, season, args.issue)
    if not family:
        print("nothing published for this issue")
        return 1
    latest = json.loads(family[-1].read_text(encoding="utf-8"))
    sections = [dict(sec) for sec in latest["sections"]]
    by_key = {sec["module_key"]: sec for sec in sections}

    edits = []
    for spec in args.replace:
        parts = spec.split("||")
        if len(parts) != 3:
            print(f"bad --replace (need SECTION||OLD||NEW): {spec}")
            return 1
        key, old, new = (p.replace("\\n", "\n") for p in parts)
        sec = by_key.get(key)
        if sec is None:
            print(f"no section '{key}' in this issue")
            return 1
        count = sec["content_md"].count(old)
        if count != 1:
            print(f"REFUSED: {old!r} occurs {count} times in '{key}'; "
                  "a correction must be unambiguous.")
            return 1
        edits.append((key, old, new))

    print(f"\n{league.slug} {season} {args.issue} — {len(edits)} identity edit(s):\n")
    for key, old, new in edits:
        print(f"  [{key}]")
        print(f"    -  {old!r}")
        print(f"    +  {new!r}\n")
    if not args.apply:
        print("dry run. Re-run with --apply to publish the correction.")
        return 0

    for key, old, new in edits:
        by_key[key]["content_md"] = by_key[key]["content_md"].replace(old, new, 1)
        src = _source_path(league.slug, season, args.issue, key)
        if src.exists() and src.read_text(encoding="utf-8").count(old) == 1:
            src.write_text(src.read_text(encoding="utf-8").replace(old, new, 1),
                           encoding="utf-8")
            print(f"updated source {src}")

    with Storage() as s:
        try:
            out = revise_issue(s, league, season, args.issue, sections=sections,
                               note=args.note)
        except PublishError as exc:
            print(f"\nCorrection refused: {exc}")
            return 1
    print(f"\ncorrection written: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
