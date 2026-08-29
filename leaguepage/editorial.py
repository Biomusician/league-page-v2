"""Load the git-tracked editorial metadata (Story Memory identity layer).

managers.json and coalitions.json are the hand-curated source of truth for
who managers are, their recurring bits, and sensitivity flags. Mutable
editorial state (bit usage log, takes) lives in SQLite instead — see
storage.py and docs/DECISIONS.md.
"""
from __future__ import annotations

import json
from pathlib import Path

from leaguepage.config import EDITORIAL_DIR


def load_managers(path: Path = EDITORIAL_DIR / "managers.json") -> dict[str, dict]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def load_coalitions(path: Path = EDITORIAL_DIR / "coalitions.json") -> dict:
    if not path.exists():
        return {"coalitions": [], "relationships": []}
    return json.loads(path.read_text(encoding="utf-8"))


def manager_for_roster(managers: dict[str, dict], league_slug: str, roster_id: int) -> list[str]:
    """Manager keys owning a roster in a league (2+ entries for co-managed teams)."""
    return [
        key
        for key, m in managers.items()
        if isinstance(m, dict)
        and (m.get("leagues") or {}).get(league_slug, {}).get("roster_id") == roster_id
    ]


def confirmed_aliases(manager: dict) -> list[str]:
    """Aliases usable as fact. The `aliases` list is confirmed by definition;
    entries in `unverified_aliases` (status inferred/rejected) never qualify."""
    return list(manager.get("aliases") or [])


def confirmed_identity_facts(manager: dict) -> dict[str, str]:
    """Identity fields with actual content, for use in generated copy."""
    identity = manager.get("identity") or {}
    return {k: v for k, v in identity.items() if v}


def confirmed_coalition_mappings(coalitions: dict) -> list[dict]:
    """Coalition→roster mappings that are confirmed (safe to state as fact)."""
    return [
        c
        for c in coalitions.get("coalitions", [])
        if (c.get("roster_mapping") or {}).get("status") == "confirmed"
    ]
