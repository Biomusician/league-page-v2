"""Seed editorial/managers.json from synced Sleeper data.

Non-destructive: existing manager entries are kept as-is (they hold
hand-curated lore); only managers not yet present are appended, and each
manager's per-league team info is refreshed from the DB. Run after a sync
whenever league membership changes.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from leaguepage.config import EDITORIAL_DIR, LEAGUES
from leaguepage.storage import Storage

MANAGERS_PATH = EDITORIAL_DIR / "managers.json"


def slugify(name: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in name.lower()).strip("-")


def blank_entry(user_id: str, display_name: str) -> dict:
    return {
        "sleeper_user_id": user_id,
        "display_name": display_name,
        "aliases": [],  # confirmed only; guesses go in unverified_aliases
        "unverified_aliases": [],
        "identity": {"nationality": "", "role": "", "notes": ""},
        "recurring_bits": [],
        "retired_bits": [],
        "notable_events": [],
        "sensitivity": "fair_game",  # fair_game | use_sparingly | do_not_use
        "leagues": {},
    }


def main() -> int:
    if MANAGERS_PATH.exists():
        managers: dict = json.loads(MANAGERS_PATH.read_text(encoding="utf-8"))
    else:
        managers = {}

    by_user_id = {m["sleeper_user_id"]: key for key, m in managers.items()}

    with Storage() as storage:
        for league in LEAGUES:
            users = {u["user_id"]: u for u in storage.get_league_users(league.league_id)}
            rosters = storage.get_rosters(league.league_id)
            for r in rosters:
                owner_ids = [oid for oid in [r.get("owner_id"), *(r.get("co_owners") or [])] if oid]
                for owner_id in owner_ids:
                    u = users.get(owner_id)
                    if not u:
                        continue
                    key = by_user_id.get(owner_id)
                    if key is None:
                        key = slugify(u.get("display_name") or owner_id)
                        managers[key] = blank_entry(owner_id, u.get("display_name") or "")
                        by_user_id[owner_id] = key
                    team_name = (u.get("metadata") or {}).get("team_name")
                    managers[key]["leagues"][league.slug] = {
                        "roster_id": r["roster_id"],
                        "team_name": team_name,
                        "co_managed": len(owner_ids) > 1,
                    }

    MANAGERS_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANAGERS_PATH.write_text(
        json.dumps(managers, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"{len(managers)} managers in {MANAGERS_PATH.relative_to(MANAGERS_PATH.parent.parent)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
