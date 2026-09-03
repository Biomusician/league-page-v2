"""Run the publication quality gate over issues and print a findings report.

    .venv/Scripts/python.exe scripts/qa_issues.py                # every published issue
    .venv/Scripts/python.exe scripts/qa_issues.py --workspace    # live editorial state
    .venv/Scripts/python.exe scripts/qa_issues.py --league disco

Read-only. Exits non-zero if any issue has blockers, so it can gate a build.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Team names carry emoji and typographic quotes; this machine's console is
# cp1252 by default and would otherwise abort mid-report.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from leaguepage import pubqa
from leaguepage.config import LEAGUES, PUBLISHED_DIR
from leaguepage.storage import Storage


def _print(rep: dict, title: str) -> None:
    print()
    print("=" * 72)
    print(f"{title}   {rep['headline']}")
    print("=" * 72)
    if rep["ignored_count"]:
        print(f"({rep['ignored_count']} warning(s) previously dismissed, not shown)")
    if not rep["groups"]:
        print("  nothing found.")
    for g in rep["groups"]:
        print(f"\n  {g['label'].upper()}")
        for f in g["findings"]:
            mark = "BLOCK" if f["severity"] == "blocker" else " warn"
            print(f"    [{mark}] {f['title']}  ({f['module_key'] or '-'})")
            print(f"            {f['detail']}")
            if f["excerpt"]:
                print(f"            current:   {f['excerpt']}")
            if f["suggestion"]:
                print(f"            suggested: {f['suggestion']}")
            for e in f["evidence"]:
                print(f"            · {e}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--league", help="disco | surfeit (default: both)")
    ap.add_argument("--workspace", action="store_true",
                    help="check the live editorial workspace instead of published snapshots")
    ap.add_argument("--issue", help="only this issue key")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    leagues = [l for l in LEAGUES if not args.league or l.slug == args.league]
    reports = []
    with Storage() as s:
        for league in leagues:
            if args.workspace:
                season = str((s.get_league(league.league_id) or {}).get("season") or "")
                keys = [args.issue] if args.issue else [
                    r["issue_key"] for r in s.list_issues(league.slug)] or ["draft"]
                for key in keys:
                    week = (int(key.removeprefix("week-"))
                            if key.startswith("week-") else None)
                    rep = pubqa.check_issue(s, league, season, key, week=week)
                    reports.append((f"{league.slug} · {season} · {key} (workspace)", rep))
            else:
                # An issue is a family (draft.json, draft.r2.json, …). Only the
                # newest revision is what readers see, so only it is audited;
                # the originals stay on disk as the historical record.
                root = PUBLISHED_DIR / league.slug
                latest: dict[tuple[str, str], dict] = {}
                for path in sorted(root.rglob("*.json")) if root.exists() else []:
                    snap = json.loads(path.read_text(encoding="utf-8"))
                    key = (snap["season"], snap.get("revises") or snap["issue_key"])
                    if int(snap.get("revision") or 1) >= int(
                            (latest.get(key) or {}).get("revision") or 0):
                        latest[key] = snap
                for (season_, issue_key), snap in sorted(latest.items()):
                    if args.issue and issue_key != args.issue:
                        continue
                    snap = dict(snap, issue_key=issue_key)
                    rep = pubqa.check_snapshot(s, league, snap)
                    rev = int(snap.get("revision") or 1)
                    stamp = (snap.get("revised_at") or snap.get("published_at") or "")[:10]
                    reports.append(
                        (f"{league.slug} · {season_} · {issue_key} "
                         + (f"(rev {rev}, {stamp})" if rev > 1
                            else f"(published {stamp})"), rep))

    if args.json:
        print(json.dumps([{"title": t, **r} for t, r in reports], indent=1))
    else:
        for title, rep in reports:
            _print(rep, title)
        print()
        tb = sum(len(r["blockers"]) for _, r in reports)
        tw = sum(len(r["warnings"]) for _, r in reports)
        print(f"TOTAL: {len(reports)} issue(s) · {tb} blocker(s) · {tw} warning(s)")
    return 1 if any(r["blockers"] for _, r in reports) else 0


if __name__ == "__main__":
    raise SystemExit(main())
