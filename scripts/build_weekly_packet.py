"""Build the weekly Matchup Lab packet for a league.

Usage:
    .venv/Scripts/python.exe scripts/build_weekly_packet.py --league surfeit --week 1

Then, to have Claude Code draft the previews, open a Claude Code session in
this repo and ask:

    Draft all unapproved matchup previews for <league> week <N> using my
    writing-style skill. Follow each matchup's generated/AUTHORING.md.

Rebuild after making Desk decisions (angles, notes, prominence) so they flow
into the packet. Rebuilding never touches commissioner_notes.md or draft.md.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from leaguepage.config import get_league
from leaguepage.matchup_packet import build_weekly_packet
from leaguepage.storage import Storage


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--league", required=True)
    parser.add_argument("--season", help="defaults to the league's current season")
    parser.add_argument("--week", type=int, required=True)
    args = parser.parse_args()

    league = get_league(args.league)
    with Storage() as storage:
        root = build_weekly_packet(storage, league, args.week)
    if root is None:
        print(f"No matchup data for {league.slug} week {args.week} — run scripts/sync.py first.")
        return 1
    print(f"Weekly packet: {root}")
    for p in sorted(root.rglob("AUTHORING.md")):
        print(f"  {p.relative_to(root).as_posix()}")
    print("\nNext: Desk decisions at /commissioner/"
          f"{league.slug}/<season>/week/{args.week}/matchups, rebuild, then ask "
          "Claude Code to draft the unapproved previews using the writing-style skill.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
