"""Draft-market value classification: REACH / STEAL and friends.

Compares each pick's slot with the league's stored reference rank (the
FantasyPros ECR snapshot loaded by leaguepage.adp; provenance travels with
the draft analysis). The internal sign convention is unchanged from
draft_analysis: delta = pick_no - reference rank, negative = taken earlier
than reference. Templates never see the raw sign; they get semantic fields
(picks_early / picks_late / label / css intensity).

The meaningful threshold is one full league round = number of teams, read
from league data, never hardcoded: 12 picks early in Disco and 10 in
Surfeit are the same one-round deviation. Visual intensity normalizes by
rounds and saturates at CAP_ROUNDS so a 200-pick outlier stays readable;
the numeric delta itself is never capped.

This measures draft-market value against a reference board. It is not a
draft grade and says nothing about whether the pick succeeds.
"""
from __future__ import annotations

ON_BOARD_TOLERANCE = 2      # |delta| <= 2 picks reads as "on board"
CAP_ROUNDS = 3.0            # visual intensity saturates at 3 full rounds

# Headline "Biggest Reaches/Steals" cover these; K/DST are excluded there
# because expert consensus ranks special teams below the draftable range
# (best K ~#202 overall on the 2026 half-PPR board) while lineups force
# every team to draft them — so overall-ECR deviation for K/DST measures
# the reference board's structure, not a roster decision. Their per-pick
# REACH/STEAL treatment on full boards is unchanged.
SKILL_POSITIONS = ("QB", "RB", "WR", "TE")
SPECIAL_TEAMS = ("K", "DEF", "DST")

CLASS_REACH = "REACH"
CLASS_EARLY = "EARLY"
CLASS_ON_BOARD = "ON BOARD"
CLASS_VALUE = "VALUE"
CLASS_STEAL = "STEAL"


def classify_pick(delta: float | None, league_size: int) -> dict | None:
    """Semantic view of one pick's delta. None when no reference rank.

    Returns picks_early/picks_late (one of them 0), rounds_early/late,
    draft_value_class, label (worded, for REACH/STEAL), short (signed
    number for table cells), intensity 0..1 for the color gradient, and
    sort_value (positive = later than reference = value/steal side).
    """
    if delta is None or not league_size:
        return None
    early = max(0.0, -delta)
    late = max(0.0, delta)
    if delta <= -league_size:
        cls = CLASS_REACH
    elif delta >= league_size:
        cls = CLASS_STEAL
    elif abs(delta) <= ON_BOARD_TOLERANCE:
        cls = CLASS_ON_BOARD
    elif delta < 0:
        cls = CLASS_EARLY
    else:
        cls = CLASS_VALUE

    n = abs(delta) / league_size          # deviation in league rounds
    intensity = round(min(n, CAP_ROUNDS) / CAP_ROUNDS, 2)

    def _p(x: float) -> str:
        return f"{x:g} pick{'' if x == 1 else 's'}"

    if cls == CLASS_REACH:
        label = f"REACH · {_p(early)} early"
    elif cls == CLASS_STEAL:
        label = f"STEAL · {_p(late)} late"
    elif cls == CLASS_ON_BOARD:
        label = "on board"
    else:
        label = f"{_p(early) + ' early' if early else _p(late) + ' late'}"
    return {
        "picks_early": early, "picks_late": late,
        "rounds_early": round(early / league_size, 2),
        "rounds_late": round(late / league_size, 2),
        "draft_value_class": cls,
        "label": label,
        "short": f"{delta:+g}",
        "intensity": intensity,
        "sort_value": delta,
    }


def headline_deviations(picks: list[dict], league_size: int,
                        *, top: int = 5) -> dict:
    """Draft-page headline lists from enriched picks (draft_analysis rows).

    skill_reaches / skill_steals: top deviations among QB/RB/WR/TE that
    actually cross the one-round threshold. special_teams: K/DST picks at
    least two rounds from reference, reported separately so mandatory
    late-position picks cannot drown out roster-construction stories."""
    rated = [p for p in picks if p.get("delta") is not None]
    skill = [p for p in rated if (p.get("position") or "").upper()
             in SKILL_POSITIONS]
    st = [p for p in rated if (p.get("position") or "").upper()
          in SPECIAL_TEAMS]
    reaches = sorted((p for p in skill if p["delta"] <= -league_size),
                     key=lambda p: p["delta"])[:top]
    steals = sorted((p for p in skill if p["delta"] >= league_size),
                    key=lambda p: -p["delta"])[:top]
    outliers = sorted((p for p in st if abs(p["delta"]) >= 2 * league_size),
                      key=lambda p: -abs(p["delta"]))[:3]
    return {"skill_reaches": reaches, "skill_steals": steals,
            "special_teams": outliers}


def position_order_context(adp, picks: list[dict], pick: dict) -> str | None:
    """Honest within-position context for a K/DST pick: where this player
    sits on the reference board at the position, and in what order the
    league actually drafted the position. Uses only the stored snapshot."""
    pos = (pick.get("position") or "").upper()
    name = pick.get("name")
    if adp is None or not name or pos not in SPECIAL_TEAMS:
        return None
    # Sleeper says DEF, FantasyPros says DST: same position
    aliases = {"DEF", "DST"} if pos in ("DEF", "DST") else {pos}
    ranked = sorted((p for p in adp.players
                     if (p.get("position") or "").upper() in aliases
                     and p.get("rank") is not None),
                    key=lambda p: p["rank"])
    from leaguepage.names import normalize_name
    idx = next((i + 1 for i, p in enumerate(ranked)
                if normalize_name(p.get("name", "")) == normalize_name(name)),
               None)
    same_pos = sorted((p for p in picks
                       if (p.get("position") or "").upper() in aliases),
                      key=lambda p: p["pick_no"])
    order = next((i + 1 for i, p in enumerate(same_pos)
                  if p["pick_no"] == pick["pick_no"]), None)
    if idx is None or order is None:
        return None
    label = "DST" if pos in ("DEF", "DST") else pos

    def _ord(n: int) -> str:
        return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"
    return f"{_ord(order)} {label} drafted · consensus {label}{idx}"


def team_draft_profile(team_summary: dict, league_size: int) -> dict:
    """Draft-market shape of one team's draft, for briefs/outlook. Input is
    a draft_analysis.summarize_team dict. Counts only picks with deltas."""
    # Skill positions only, for the reason in the calibration note below:
    # averaging in the K/DST tax measures the reference board's shape, not
    # how this manager drafts, and it flipped at least one real team's
    # consensus label.
    deltas = [p["delta"] for p in team_summary.get("picks_by_round", [])
              if p.get("delta") is not None and not p.get("off_board")
              and (p.get("position") or "").upper() in SKILL_POSITIONS]
    reaches = sum(1 for d in deltas if d <= -league_size)
    steals = sum(1 for d in deltas if d >= league_size)
    mean_dev = (sum(abs(d) for d in deltas) / len(deltas)) if deltas else None
    return {
        "rated_picks": len(deltas),
        "reach_picks": reaches,
        "steal_picks": steals,
        "biggest_reach": team_summary.get("biggest_reach"),
        "biggest_steal": team_summary.get("biggest_value"),
        "mean_abs_rounds": (round(mean_dev / league_size, 2)
                            if mean_dev is not None else None),
    }


def consensus_style(profiles: dict[int, dict]) -> dict[int, str | None]:
    """Label unusually consensus-following/defying drafts relative to the
    league: outside ~1.5x / under ~0.6x the league median mean deviation."""
    devs = {rid: p["mean_abs_rounds"] for rid, p in profiles.items()
            if p["mean_abs_rounds"] is not None}
    if len(devs) < 4:
        return {rid: None for rid in profiles}
    ordered = sorted(devs.values())
    med = ordered[len(ordered) // 2]
    out: dict[int, str | None] = {}
    for rid, p in profiles.items():
        d = p["mean_abs_rounds"]
        if d is None or med == 0:
            out[rid] = None
        elif d >= 1.5 * med:
            out[rid] = "consensus-defying"
        elif d <= 0.6 * med:
            out[rid] = "consensus-following"
        else:
            out[rid] = None
    return out


# ---------------------------------------------- team-level market value
#
# The calibration decision (docs/DECISIONS.md, 2026-08-30) applied to a whole
# team's draft rather than one pick. Summing every delta on a board that
# ranks kickers and defenses below the draftable range does not measure who
# drafted well — it measures who paid a structural tax that the reference
# board imposes on everybody. In The Surfeit's 2026 draft that tax was 83%
# of all deviation, so the ranking was mostly a kicker table.


def team_market_split(picks: list[dict]) -> dict:
    """Skill-position market value, the special-teams tax, and the raw total.

    `skill` is the number that reflects roster decisions. `special_teams` is
    reported beside it, never folded in, because a team cannot choose not to
    draft a kicker."""
    rated = [p for p in picks if p.get("delta") is not None]
    skill = [p for p in rated if (p.get("position") or "").upper() in SKILL_POSITIONS]
    st = [p for p in rated if (p.get("position") or "").upper() in SPECIAL_TEAMS]
    return {
        "skill": sum(p["delta"] for p in skill),
        "special_teams": sum(p["delta"] for p in st),
        "total": sum(p["delta"] for p in rated),
        "skill_picks": len(skill),
        "special_teams_picks": len(st),
    }


def market_value_ranking(teams: list[dict], names: dict[int, str]) -> dict:
    """Both orderings side by side, so a change in method is auditable.

    `teams` are draft_analysis team summaries. Returns rows carrying each
    team's raw-total rank, its skill-only rank, and the movement between
    them, plus how much of the league's total deviation was special teams —
    the number that says whether the distinction mattered here at all."""
    rows = []
    for t in teams:
        rid = t["roster_id"]
        split = team_market_split(t.get("picks_by_round", []))
        rows.append({"roster_id": rid, "name": names.get(rid, f"Roster {rid}"),
                     **split})
    by_total = sorted(rows, key=lambda r: (-r["total"], r["roster_id"]))
    by_skill = sorted(rows, key=lambda r: (-r["skill"], r["roster_id"]))
    raw_rank = {r["roster_id"]: i + 1 for i, r in enumerate(by_total)}
    skill_rank = {r["roster_id"]: i + 1 for i, r in enumerate(by_skill)}
    for r in rows:
        r["raw_rank"] = raw_rank[r["roster_id"]]
        r["skill_rank"] = skill_rank[r["roster_id"]]
        r["movement"] = raw_rank[r["roster_id"]] - skill_rank[r["roster_id"]]
    total_abs = sum(abs(r["total"]) for r in rows)
    st_abs = sum(abs(r["special_teams"]) for r in rows)
    moved = [r for r in rows if r["movement"]]
    return {
        "rows": sorted(rows, key=lambda r: r["skill_rank"]),
        "special_teams_share": (st_abs / total_abs) if total_abs else 0.0,
        "teams_moved": len(moved),
        "largest_move": max((abs(r["movement"]) for r in rows), default=0),
        # "Material" means the order a reader would quote actually changes:
        # somebody moves, and special teams carry a real share of the spread.
        "material": bool(moved) and (st_abs / total_abs if total_abs else 0) >= 0.25,
    }
