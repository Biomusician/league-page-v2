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
        team_name = None
        for oid in [roster.get("owner_id"), *(roster.get("co_owners") or [])]:
            u = users.get(oid) or {}
            team_name = team_name or (u.get("metadata") or {}).get("team_name")
        override = overrides.get(rid)
        # a neutral "Roster N" placeholder override yields to a real Sleeper
        # name the moment the manager sets one; real overrides are preserved
        if override and not (override == f"Roster {rid}" and team_name):
            out[rid] = {"name": override, "source": "commissioner"}
        elif team_name:
            out[rid] = {"name": team_name.strip(), "source": "sleeper-team-name"}
        elif override:
            out[rid] = {"name": override, "source": "commissioner"}
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


def sleeper_team_names(storage: Storage, league: League) -> dict[int, str | None]:
    """roster_id -> current Sleeper fantasy-team name (metadata.team_name via
    owner, then co-owners). Never a login handle."""
    users = {u["user_id"]: u for u in storage.get_league_users(league.league_id)}
    out: dict[int, str | None] = {}
    for roster in storage.get_rosters(league.league_id):
        name = None
        for oid in [roster.get("owner_id"), *(roster.get("co_owners") or [])]:
            u = users.get(oid) or {}
            name = name or (u.get("metadata") or {}).get("team_name")
        out[roster["roster_id"]] = name.strip() if name else None
    return out


def identity_rows(storage: Storage, league: League, *,
                  player_values: dict[str, dict] | None = None) -> list[dict]:
    """Everything the PRIVATE Desk needs to identify a roster at a glance:
    Sleeper team name, owner/co-owner display names (private context, never
    for public output), first-round draft slot, top rostered players, the
    current public name and its source, and whether a commissioner override
    now differs from the Sleeper name (rename detection)."""
    users = {u["user_id"]: u for u in storage.get_league_users(league.league_id)}
    overrides = storage.get_public_team_names(league.slug)
    sleeper = sleeper_team_names(storage, league)
    resolved = resolve_public_names(storage, league)
    slots: dict[int, int] = {}
    drafts = storage.get_drafts_for_league(league.league_id)
    if drafts:
        for p in storage.get_draft_picks(drafts[0]["draft_id"]):
            if p.get("round") == 1 and p.get("roster_id") is not None:
                slots[p["roster_id"]] = p["pick_no"]
    rows = []
    for roster in sorted(storage.get_rosters(league.league_id),
                         key=lambda r: r["roster_id"]):
        rid = roster["roster_id"]
        owners = [(users.get(oid) or {}).get("display_name") or "?"
                  for oid in [roster.get("owner_id"), *(roster.get("co_owners") or [])]
                  if oid]
        top = []
        if player_values:
            ps = sorted((player_values[pid] for pid in (roster.get("players") or [])
                         if pid in player_values), key=lambda p: -p["value"])[:3]
            top = [f"{p['name']} ({p['position']})" for p in ps]
        rows.append({
            "roster_id": rid,
            "sleeper_name": sleeper.get(rid),
            "owners": owners,                       # PRIVATE context
            "co_managed": bool(roster.get("co_owners")),
            "draft_slot": slots.get(rid),
            "top_players": top,
            "public_name": resolved[rid]["name"],
            "source": resolved[rid]["source"],
            "override": overrides.get(rid),
            "renamed_on_sleeper": bool(overrides.get(rid) and sleeper.get(rid)
                                       and overrides[rid] != sleeper[rid]),
        })
    return rows
