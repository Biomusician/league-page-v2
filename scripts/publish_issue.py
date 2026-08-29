"""Issue lifecycle CLI: edited -> approved -> published.

    # after editing draft-issue.md into issue.md (marker removed):
    .venv/Scripts/python.exe scripts/publish_issue.py --league surfeit --issue draft --mark-edited
    .venv/Scripts/python.exe scripts/publish_issue.py --league surfeit --issue draft --approve
    .venv/Scripts/python.exe scripts/publish_issue.py --league surfeit --issue draft --publish

Publishing renders site/<league>/<season>/<issue>.html. Generated prose can
never publish directly: approval is explicit and the ROUGH DRAFT marker blocks.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from leaguepage.config import get_league
from leaguepage.publish import PublishError, approve, mark_edited, publish_issue
from leaguepage.storage import Storage


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--league", required=True)
    parser.add_argument("--season")
    parser.add_argument("--issue", default="draft")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--mark-edited", action="store_true")
    action.add_argument("--approve", action="store_true")
    action.add_argument("--publish", action="store_true")
    action.add_argument("--status", action="store_true", help="show current lifecycle status")
    args = parser.parse_args()

    league = get_league(args.league)
    with Storage() as storage:
        season = args.season or str((storage.get_league(league.league_id) or {}).get("season") or "")
        try:
            if args.status:
                issue = storage.get_issue(league.slug, season, args.issue)
                print(issue["status"] if issue else "not started")
            elif args.mark_edited:
                src = mark_edited(storage, league, season, args.issue)
                print(f"edited: {src}")
            elif args.approve:
                approve(storage, league, season, args.issue)
                print("approved")
            elif args.publish:
                out = publish_issue(storage, league, season, args.issue)
                print(f"published: {out}")
        except PublishError as exc:
            print(f"REFUSED: {exc}")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
