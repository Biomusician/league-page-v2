"""Build a Claude Code editorial packet.

Usage:
    .venv/Scripts/python.exe scripts/build_editorial_packet.py --league surfeit --type draft

Emits editorial/<season>/<league>/draft/generated/ (see leaguepage/packet.py).
Rerunnable; deterministic except MANIFEST.json's timestamp.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from leaguepage.config import get_league
from leaguepage.packet import build_draft_packet
from leaguepage.storage import Storage


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--league", required=True, help="league slug: disco | surfeit")
    parser.add_argument("--type", default="draft", choices=["draft"],
                        help="packet type (weekly types arrive with Matchup Lab)")
    args = parser.parse_args()

    league = get_league(args.league)
    with Storage() as storage:
        out = build_draft_packet(storage, league)
    if out is None:
        print(f"No draft data for {league.slug} — run scripts/sync.py after the draft.")
        return 1
    files = sorted(p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file())
    print(f"Packet: {out}")
    for f in files:
        print(f"  {f}")
    print(f"\nNext: open a Claude Code session in this repo and point it at "
          f"{out / 'AUTHORING_BRIEF.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
