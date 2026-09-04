"""Deterministic draft analytics.

Everything here is a computable fact with provenance — no editorial judgment.
A pick taken 18 spots before its reference rank is reported as exactly that;
whether it was a "questionable reach" is for the commissioner/editorial layer.

Output is plain dicts (JSON-serializable) because the editorial packet writes
them straight to analytics.json. Handles completed, in-progress, and empty
drafts, missing reference ranks, unmatched players, and co-managed teams; all
sizes derive from the league payload (works for 10-team Surfeit and 12-team
Disco alike).
"""
from __future__ import annotations

import re
from collections import defaultdict
from statistics import median

from leaguepage import evidence
from leaguepage.adp import ADPSource
from leaguepage.config import League
from leaguepage.draft_value import SKILL_POSITIONS
from leaguepage.editorial import manager_for_roster
from leaguepage.storage import Storage

# Positions that count as skill/priority positions for "ignored long" checks
CORE_POSITIONS = ("QB", "RB", "WR", "TE")


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return slug or "team"


def _team_identity(
    league: League,
    roster: dict,
    users: dict[str, dict],
    managers: dict[str, dict],
) -> dict:
    """Resolve a roster to team name + manager identity (co-managers included)."""
    owner_ids = [oid for oid in [roster.get("owner_id"), *(roster.get("co_owners") or [])] if oid]
    display_names = []
    team_name = None
    for oid in owner_ids:
        u = users.get(oid)
        if not u:
            continue
        display_names.append(u.get("display_name") or oid)
        team_name = team_name or (u.get("metadata") or {}).get("team_name")
    manager_keys = manager_for_roster(managers, league.slug, roster["roster_id"]) if managers else []
    return {
        "roster_id": roster["roster_id"],
        "team_name": team_name,
        "display_names": display_names,
        "manager_keys": manager_keys,
        "co_managed": len(owner_ids) > 1,
        "evidence": [evidence.roster_ref(league.league_id, roster["roster_id"])]
        + [evidence.manager_ref(k) for k in manager_keys],
    }


def _pick_player_fields(pick: dict, storage: Storage) -> dict:
    md = pick.get("metadata") or {}
    name = " ".join(filter(None, [md.get("first_name"), md.get("last_name")])).strip()
    position = md.get("position")
    nfl_team = md.get("team")
    if not name and pick.get("player_id"):
        p = storage.get_player(pick["player_id"]) or {}
        name = p.get("full_name") or " ".join(
            filter(None, [p.get("first_name"), p.get("last_name")])
        )
        position = position or p.get("position")
        nfl_team = nfl_team or p.get("team")
    return {"name": name or f"player:{pick.get('player_id')}", "position": position, "nfl_team": nfl_team}


def choose_draft(storage: Storage, league: League, season: str) -> dict | None:
    """The season's draft: prefer completed, else the one with the most picks."""
    drafts = [d for d in storage.get_drafts_for_league(league.league_id)
              if str(d.get("season")) == str(season)]
    if not drafts:
        return None
    drafts.sort(
        key=lambda d: (d.get("status") == "complete", len(storage.get_draft_picks(d["draft_id"]))),
        reverse=True,
    )
    return drafts[0]


def enrich_picks(
    storage: Storage,
    league: League,
    season: str,
    draft: dict,
    picks: list[dict],
    teams: dict[int, dict],
    adp: ADPSource | None,
) -> tuple[list[dict], list[str]]:
    """Attach player identity, team identity, and reference-rank deltas."""
    n_picks = len(picks)
    enriched: list[dict] = []
    unmatched: list[str] = []
    for pick in picks:
        fields = _pick_player_fields(pick, storage)
        roster_id = pick.get("roster_id")
        team = teams.get(roster_id, {})
        row = {
            "league": league.slug,
            "season": season,
            "draft_id": draft["draft_id"],
            "round": pick.get("round"),
            "pick_no": pick.get("pick_no"),
            "draft_slot": pick.get("draft_slot"),
            "roster_id": roster_id,
            "team_slug": team.get("team_slug"),
            "player_id": pick.get("player_id"),
            "name": fields["name"],
            "position": fields["position"],
            "nfl_team": fields["nfl_team"],
            "managers": team.get("manager_keys", []),
            "manager_display_names": team.get("display_names", []),
            "adp_source": None,
            "adp": None,
            "delta": None,  # pick_no - reference rank: negative = ahead of rank
            "evidence": [evidence.pick_ref(draft["draft_id"], pick["pick_no"])],
        }
        if adp is not None:
            rank = adp.lookup(fields["name"], fields["position"])
            if rank is not None:
                row["adp_source"] = adp.source_key
                row["adp"] = rank
                row["delta"] = round(pick["pick_no"] - rank, 1)
                # A reference rank past the end of the draft is not a
                # position anybody could have drafted from. "REACH · 244
                # picks early" in a 228-pick draft is a fact about where the
                # board stops, not about the manager. In The Surfeit 16% of
                # picks sit off the board, most of them K and DEF.
                row["off_board"] = rank > n_picks
                row["evidence"].append(evidence.adp_ref(adp.source_key, fields["name"]))
            else:
                unmatched.append(fields["name"])
        enriched.append(row)
    return enriched, unmatched


def summarize_team(
    league: League,
    season: str,
    team: dict,
    picks: list[dict],
    starters_count: int,
    rounds: int | None,
) -> dict:
    """Factual summary of one team's draft. `picks` are this team's, in order."""
    by_pos: dict[str, list[dict]] = defaultdict(list)
    for p in picks:
        by_pos[p["position"] or "?"].append(p)

    position_counts = {pos: len(ps) for pos, ps in sorted(by_pos.items())}
    first_by_position = {
        pos: {"round": ps[0]["round"], "pick_no": ps[0]["pick_no"], "name": ps[0]["name"]}
        for pos, ps in by_pos.items()
    }
    picks_by_round = [
        {"round": p["round"], "pick_no": p["pick_no"], "name": p["name"],
         "position": p["position"], "nfl_team": p["nfl_team"], "adp": p["adp"],
         "delta": p["delta"],
         # Whether the reference rank falls past the end of the draft. This
         # whitelist dropped it, so the label downstream still quoted a
         # magnitude the board cannot support.
         "off_board": p.get("off_board", False)}
        for p in picks
    ]

    # early/late concentration: first 3 rounds and everything after the
    # starter-range rounds (round > number of starting slots ≈ bench picks)
    early = [p for p in picks if (p["round"] or 0) <= 3]
    early_positions = defaultdict(int)
    for p in early:
        early_positions[p["position"] or "?"] += 1
    bench_range = [p for p in picks if (p["round"] or 0) > starters_count]
    bench_positions = defaultdict(int)
    for p in bench_range:
        bench_positions[p["position"] or "?"] += 1

    # stacks: drafted QB + drafted pass catcher on the same NFL team
    stacks = []
    for qb in by_pos.get("QB", []):
        partners = [
            p for p in picks
            if p["position"] in ("WR", "TE")
            and p["nfl_team"] and p["nfl_team"] == qb["nfl_team"]
        ]
        if partners:
            stacks.append({
                "qb": qb["name"],
                "partners": [p["name"] for p in partners],
                "nfl_team": qb["nfl_team"],
                "evidence": qb["evidence"] + [e for p in partners for e in p["evidence"]],
            })

    # same-NFL-team concentration (3+ players)
    nfl_counts: dict[str, list[dict]] = defaultdict(list)
    for p in picks:
        if p["nfl_team"]:
            nfl_counts[p["nfl_team"]].append(p)
    concentration = [
        {"nfl_team": t, "count": len(ps), "players": [p["name"] for p in ps],
         "evidence": [e for p in ps for e in p["evidence"]]}
        for t, ps in sorted(nfl_counts.items(), key=lambda kv: -len(kv[1]))
        if len(ps) >= 3
    ]

    with_delta = [p for p in picks if p["delta"] is not None]
    reaches = sorted((p for p in with_delta if p["delta"] < 0), key=lambda p: p["delta"])
    values = sorted((p for p in with_delta if p["delta"] > 0), key=lambda p: -p["delta"])

    def _delta_fact(p: dict) -> dict:
        return {
            "name": p["name"], "position": p["position"], "round": p["round"],
            "pick_no": p["pick_no"], "adp": p["adp"], "delta": p["delta"],
            "adp_source": p["adp_source"], "evidence": p["evidence"],
        }

    return {
        "league": league.slug,
        "season": season,
        "roster_id": team["roster_id"],
        "team_slug": team["team_slug"],
        "team_name": team["team_name"],
        "manager_keys": team["manager_keys"],
        "manager_display_names": team["display_names"],
        "co_managed": team["co_managed"],
        "pick_count": len(picks),
        "position_counts": position_counts,
        "first_pick_by_position": first_by_position,
        "picks_by_round": picks_by_round,
        "early_rounds_positions": dict(early_positions),
        "bench_range_positions": dict(bench_positions),
        "bench_range_definition": f"rounds {starters_count + 1}+ of {rounds or '?'} (starter slots: {starters_count})",
        "stacks": stacks,
        "nfl_team_concentration": concentration,
        "reaches": [_delta_fact(p) for p in reaches[:5]],
        "values": [_delta_fact(p) for p in values[:5]],
        "biggest_reach": _delta_fact(reaches[0]) if reaches else None,
        "biggest_value": _delta_fact(values[0]) if values else None,
        "evidence": team["evidence"],
    }


def _league_anomalies(team_summaries: list[dict], starters_count: int) -> None:
    """Attach cross-team factual anomalies (position ignored unusually long)
    to each team summary. Mutates in place; phrased as facts, not judgments."""
    for pos in CORE_POSITIONS:
        firsts = {
            t["team_slug"]: (t["first_pick_by_position"].get(pos) or {}).get("round")
            for t in team_summaries
        }
        drafted_rounds = [r for r in firsts.values() if r is not None]
        if not drafted_rounds:
            continue
        med = median(drafted_rounds)
        for t in team_summaries:
            t.setdefault("anomalies", [])
            r = firsts[t["team_slug"]]
            if r is None:
                t["anomalies"].append({
                    "fact": f"Drafted no {pos} (league median first {pos}: round {med:g}).",
                    "metric": f"first-{pos.lower()}-round",
                })
            elif r >= med + 3:
                t["anomalies"].append({
                    "fact": f"First {pos} in round {r}; league median is round {med:g}.",
                    "metric": f"first-{pos.lower()}-round",
                })
    for t in team_summaries:
        t.setdefault("anomalies", [])


def analyze_league_draft(
    storage: Storage,
    league: League,
    *,
    managers: dict[str, dict] | None = None,
    adp: ADPSource | None = None,
) -> dict | None:
    """Full deterministic analysis of a league's current-season draft.
    Returns None when the league has no draft record at all."""
    league_data = storage.get_league(league.league_id) or {}
    season = str(league_data.get("season") or "")
    draft = choose_draft(storage, league, season)
    if draft is None:
        return None

    picks = storage.get_draft_picks(draft["draft_id"])
    rosters = storage.get_rosters(league.league_id)
    users = {u["user_id"]: u for u in storage.get_league_users(league.league_id)}
    roster_positions = league_data.get("roster_positions") or []
    starters_count = len([s for s in roster_positions if s != "BN"])
    rounds = (draft.get("settings") or {}).get("rounds")
    total_teams = league_data.get("total_rosters") or len(rosters)

    teams: dict[int, dict] = {}
    used_slugs: set[str] = set()
    for r in rosters:
        identity = _team_identity(league, r, users, managers or {})
        # Privacy: slugs appear in committed paths and URLs, so an unnamed
        # team falls back to roster-N, never to the manager's Sleeper handle.
        base = slugify(identity["team_name"]) if identity["team_name"] else f"roster-{r['roster_id']}"
        slug = base
        if slug in used_slugs:
            slug = f"{base}-{r['roster_id']}"
        used_slugs.add(slug)
        identity["team_slug"] = slug
        teams[r["roster_id"]] = identity

    enriched, unmatched = enrich_picks(storage, league, season, draft, picks, teams, adp)

    warnings: list[str] = []
    status = draft.get("status")
    expected = (rounds or 0) * total_teams
    if status != "complete":
        warnings.append(f"Draft status is '{status}' — analysis reflects {len(picks)} picks so far.")
    if expected and len(picks) < expected and status == "complete":
        warnings.append(f"Draft marked complete but has {len(picks)} of {expected} expected picks.")
    if adp is None:
        warnings.append("No reference-rank (ADP) source configured or found — deltas unavailable.")
    elif unmatched:
        warnings.append(
            f"{len(unmatched)} drafted players had no entry in {adp.source_key}; "
            "their deltas are omitted (never fabricated)."
        )
    for rid, t in teams.items():
        if not t["display_names"]:
            warnings.append(f"Roster {rid} has no resolvable owner.")

    picks_by_roster: dict[int, list[dict]] = defaultdict(list)
    for p in enriched:
        if p["roster_id"] is not None:
            picks_by_roster[p["roster_id"]].append(p)
    for ps in picks_by_roster.values():
        ps.sort(key=lambda p: p["pick_no"])

    team_summaries = [
        summarize_team(league, season, teams[rid], picks_by_roster.get(rid, []), starters_count, rounds)
        for rid in sorted(teams)
    ]
    if picks:  # anomaly medians are meaningless on an empty draft
        _league_anomalies(team_summaries, starters_count)
    else:
        for t in team_summaries:
            t.setdefault("anomalies", [])

    # The boldest picks in the league are a claim about judgment, so they
    # are drawn from skill positions only and from picks the board could
    # actually rank. Unfiltered, four of Surfeit's five biggest reaches were
    # kickers and defenses that everyone is forced to draft below the board.
    with_delta = [p for p in enriched
                  if p["delta"] is not None and not p.get("off_board")
                  and (p.get("position") or "").upper() in SKILL_POSITIONS]
    league_reaches = sorted((p for p in with_delta if p["delta"] < 0), key=lambda p: p["delta"])[:5]
    league_values = sorted((p for p in with_delta if p["delta"] > 0), key=lambda p: -p["delta"])[:5]

    return {
        "league": league.slug,
        "league_name": league_data.get("name"),
        "season": season,
        "draft_id": draft["draft_id"],
        "draft_status": status,
        "draft_type": draft.get("type"),
        "rounds": rounds,
        "total_teams": total_teams,
        "starters_count": starters_count,
        "roster_positions": roster_positions,
        "pick_count": len(picks),
        "expected_pick_count": expected or None,
        "adp_source": adp.source_key if adp else None,
        "adp_provenance": adp.provenance_label() if adp else None,
        "unmatched_adp_players": sorted(set(unmatched)),
        "warnings": warnings,
        "picks": enriched,
        "teams": team_summaries,
        "league_biggest_reaches": [
            {"name": p["name"], "team_slug": p["team_slug"], "pick_no": p["pick_no"],
             "adp": p["adp"], "delta": p["delta"], "adp_source": p["adp_source"],
             "evidence": p["evidence"]}
            for p in league_reaches
        ],
        "league_biggest_values": [
            {"name": p["name"], "team_slug": p["team_slug"], "pick_no": p["pick_no"],
             "adp": p["adp"], "delta": p["delta"], "adp_source": p["adp_source"],
             "evidence": p["evidence"]}
            for p in league_values
        ],
        "evidence": [evidence.league_ref(league.league_id)],
    }
