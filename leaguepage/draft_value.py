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


def team_draft_profile(team_summary: dict, league_size: int) -> dict:
    """Draft-market shape of one team's draft, for briefs/outlook. Input is
    a draft_analysis.summarize_team dict. Counts only picks with deltas."""
    deltas = [p["delta"] for p in team_summary.get("picks_by_round", [])
              if p.get("delta") is not None]
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
