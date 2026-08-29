"""Reference-rank ("ADP") source abstraction.

Snapshots live in refdata/adp/<source_key>.json (git-tracked so every delta
the app ever showed stays reproducible). Standard shape:

    {
      "source_key": "...", "source_name": "...", "kind": "expert-consensus-rank",
      "scoring_format": "...", "retrieved_at": "...", "imported_at": "...",
      "note": "...",
      "players": [{"name": str, "position": str, "team": str|null, "rank": number}]
    }

Rules: a missing snapshot or unmatched player yields None — never a fabricated
value — and every delta shown downstream must carry provenance_label().
scripts/import_adp.py creates snapshots (from Fantasy Bot's cache or any CSV).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from leaguepage.config import REPO_ROOT
from leaguepage.names import normalize_name

ADP_DIR = REPO_ROOT / "refdata" / "adp"


@dataclass
class ADPSource:
    source_key: str
    source_name: str
    kind: str
    scoring_format: str
    retrieved_at: str
    note: str
    players: list[dict]
    _by_name_pos: dict[tuple[str, str], float] = field(default_factory=dict, repr=False)
    _by_name: dict[str, float] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        for p in self.players:
            key = normalize_name(p.get("name", ""))
            if not key or p.get("rank") is None:
                continue
            rank = float(p["rank"])
            pos = (p.get("position") or "").upper()
            self._by_name_pos.setdefault((key, pos), rank)
            # name-only fallback: on collision keep the better (lower) rank,
            # which is the player a drafter almost certainly means
            if key not in self._by_name or rank < self._by_name[key]:
                self._by_name[key] = rank

    def lookup(self, name: str, position: str | None = None) -> float | None:
        key = normalize_name(name)
        if not key:
            return None
        if position:
            rank = self._by_name_pos.get((key, position.upper()))
            if rank is not None:
                return rank
        return self._by_name.get(key)

    def provenance_label(self) -> str:
        return f"{self.source_name} (retrieved {self.retrieved_at[:10]})"


def load_adp_source(source_key: str, adp_dir: Path = ADP_DIR) -> ADPSource | None:
    path = adp_dir / f"{source_key}.json"
    if not path.exists():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    return ADPSource(
        source_key=raw["source_key"],
        source_name=raw["source_name"],
        kind=raw.get("kind", "unknown"),
        scoring_format=raw.get("scoring_format", ""),
        retrieved_at=raw.get("retrieved_at", ""),
        note=raw.get("note", ""),
        players=raw.get("players", []),
    )


def load_adp_for_league(league, adp_dir: Path = ADP_DIR) -> ADPSource | None:
    """League objects carry their preferred source key; None if unset/missing."""
    if not getattr(league, "adp_source", ""):
        return None
    return load_adp_source(league.adp_source, adp_dir)
