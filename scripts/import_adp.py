"""Import reference-rank ("ADP") snapshots into refdata/adp/.

Two modes:

1. Copy today's FantasyPros ECR snapshots from Fantasy Bot's rankings cache
   (the leagues' formats: half-PPR 1QB for Surfeit, superflex for Disco):

       .venv/Scripts/python.exe scripts/import_adp.py --from-fantasy-bot

2. Import any CSV with columns name,position,team,rank:

       .venv/Scripts/python.exe scripts/import_adp.py --csv path.csv \
           --source-key my_adp --source-name "Underdog ADP" \
           --scoring-format half_ppr --kind adp [--retrieved 2026-08-29]

Snapshots are git-tracked so historical deltas stay reproducible.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from leaguepage.adp import ADP_DIR

FANTASY_BOT_CACHE = Path(r"C:\Users\Jonathan\Fantasy Bot\data\rankings_cache")

FANTASY_BOT_SOURCES = [
    # (cache file, source_key, source_name, scoring_format)
    (
        "fantasypros_redraft_half_ppr.json",
        "fantasypros_ecr_redraft_half_ppr",
        "FantasyPros Expert Consensus Rank (redraft, half PPR)",
        "half_ppr_1qb",
    ),
    (
        "fantasypros_redraft_superflex.json",
        "fantasypros_ecr_redraft_superflex",
        "FantasyPros Expert Consensus Rank (redraft, superflex)",
        "half_ppr_superflex",
    ),
]

ECR_NOTE = (
    "Expert consensus rank used as the draft-position reference; it is a "
    "ranking, not observed ADP. Every delta shown downstream must name this "
    "source."
)


def write_snapshot(payload: dict) -> Path:
    ADP_DIR.mkdir(parents=True, exist_ok=True)
    out = ADP_DIR / f"{payload['source_key']}.json"
    out.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return out


def import_fantasy_bot() -> int:
    failures = 0
    for cache_name, source_key, source_name, scoring_format in FANTASY_BOT_SOURCES:
        cache_path = FANTASY_BOT_CACHE / cache_name
        if not cache_path.exists():
            print(f"FAIL  missing {cache_path}")
            failures += 1
            continue
        raw = json.loads(cache_path.read_text(encoding="utf-8"))
        players = [
            {
                "name": p.get("name"),
                "position": p.get("position"),
                "team": p.get("team"),
                "rank": p.get("rank_ecr"),
            }
            for p in raw.get("payload", [])
            if p.get("name") and p.get("rank_ecr") is not None
        ]
        out = write_snapshot({
            "source_key": source_key,
            "source_name": source_name,
            "kind": "expert-consensus-rank",
            "scoring_format": scoring_format,
            "retrieved_at": raw.get("fetched_at", ""),
            "imported_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "note": ECR_NOTE,
            "origin": f"Fantasy Bot rankings cache: {cache_name}",
            "players": players,
        })
        print(f"wrote {out.name} ({len(players)} players, retrieved {raw.get('fetched_at', '?')[:10]})")
    return failures


def import_csv(args: argparse.Namespace) -> int:
    rows = []
    with open(args.csv, newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            if not row.get("name") or not row.get("rank"):
                continue
            rows.append({
                "name": row["name"].strip(),
                "position": (row.get("position") or "").strip().upper() or None,
                "team": (row.get("team") or "").strip().upper() or None,
                "rank": float(row["rank"]),
            })
    out = write_snapshot({
        "source_key": args.source_key,
        "source_name": args.source_name,
        "kind": args.kind,
        "scoring_format": args.scoring_format,
        "retrieved_at": args.retrieved or dt.date.today().isoformat(),
        "imported_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "note": args.note or "",
        "origin": f"CSV import: {args.csv}",
        "players": rows,
    })
    print(f"wrote {out.name} ({len(rows)} players)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--from-fantasy-bot", action="store_true")
    parser.add_argument("--csv")
    parser.add_argument("--source-key")
    parser.add_argument("--source-name")
    parser.add_argument("--scoring-format", default="")
    parser.add_argument("--kind", default="adp")
    parser.add_argument("--retrieved")
    parser.add_argument("--note")
    args = parser.parse_args()

    if args.from_fantasy_bot:
        return import_fantasy_bot()
    if args.csv:
        if not (args.source_key and args.source_name):
            parser.error("--csv requires --source-key and --source-name")
        return import_csv(args)
    parser.error("choose --from-fantasy-bot or --csv")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
