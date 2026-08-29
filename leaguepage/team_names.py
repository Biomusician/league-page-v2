"""Public team-name resolution (Phase 5.1B safeguard).

Three kinds of team identity exist:
  1. canonical public display name — commissioner-confirmed via the Desk
     (team_names table), or a Sleeper team name (safe: managers set those
     for the league to see);
  2. league nickname / confirmed alias — editorial metadata, usable in prose;
  3. temporary test/editorial label — allowed only in TEST/ROUGH material.

A roster with neither (1) source resolves to None and BLOCKS publication with
an actionable message. Sleeper *handles* are never a public name.
"""
from __future__ import annotations

from leaguepage.config import League
from leaguepage.storage import Storage


def resolve_public_names(storage: Storage, league: League) -> dict[int, dict]:
    """roster_id -> {name, source} where source is 'commissioner' or
    'sleeper-team-name'; unresolved rosters get {name: None, source: None}."""
    overrides = storage.get_public_team_names(league.slug)
    users = {u["user_id"]: u for u in storage.get_league_users(league.league_id)}
    out: dict[int, dict] = {}
    for roster in storage.get_rosters(league.league_id):
        rid = roster["roster_id"]
        if rid in overrides:
            out[rid] = {"name": overrides[rid], "source": "commissioner"}
            continue
        team_name = None
        for oid in [roster.get("owner_id"), *(roster.get("co_owners") or [])]:
            u = users.get(oid) or {}
            team_name = team_name or (u.get("metadata") or {}).get("team_name")
        if team_name:
            out[rid] = {"name": team_name, "source": "sleeper-team-name"}
        else:
            out[rid] = {"name": None, "source": None}
    return out


def unresolved_rosters(storage: Storage, league: League) -> list[int]:
    return [rid for rid, v in resolve_public_names(storage, league).items()
            if v["name"] is None]


def require_public_names(storage: Storage, league: League, roster_ids: list[int] | None = None) -> dict[int, str]:
    """Names for publication; raises with an actionable message if any roster
    involved lacks a confirmed public display name."""
    resolved = resolve_public_names(storage, league)
    wanted = roster_ids if roster_ids is not None else list(resolved)
    missing = [rid for rid in wanted if resolved.get(rid, {}).get("name") is None]
    if missing:
        details = "; ".join(f"Roster {rid} has no confirmed public display name." for rid in missing)
        raise ValueError(
            f"{details} Set names on the Commissioner's Desk (week workspace, Team names panel)."
        )
    return {rid: resolved[rid]["name"] for rid in wanted}
