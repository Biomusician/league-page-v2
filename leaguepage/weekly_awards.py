"""Weekly award NOMINATIONS — the system nominates, the commissioner awards.

Every nomination states its deterministic basis and evidence. Subjective
categories (Manager of the Week, Galaxy Brain) present evidence without
pretending management quality is mathematically proven. Hindsight metrics
(Shame) state what happened; whether the process deserves the award is the
commissioner's call. With no played games, every award slate is 'none' —
awards are never manufactured.

Sleeper's public API has no projections, so Upset of the Week uses labeled
alternatives: pre-week standings differential, or the commissioner's
Peer and Near-Peer ranking when standings are meaningless (week 1).
"""
from __future__ import annotations

from leaguepage import evidence
from leaguepage.config import League
from leaguepage.draft_value import SPECIAL_TEAMS
from leaguepage.matchup_analysis import FLEX_ELIGIBLE, analyze_week, faab_cost
from leaguepage.storage import Storage

# thresholds are editorial knobs, stated in each nomination's basis
SHAME_MIN_DELTA = 10.0
HEIST_MIN_POINTS = 12.0
BENCH_MEMORIAL_MIN = 20.0
FAAB_ARSON_PCT = 0.20
MERCY_STRONG_MARGIN = 40.0
UPSET_MIN_STANDINGS_GAP = 4


def _slot_layout(roster_positions: list[str]) -> list[str]:
    return [s for s in roster_positions if s != "BN"]


def _eligible(player_pos: str, slot: str) -> bool:
    if slot in FLEX_ELIGIBLE:
        return player_pos in FLEX_ELIGIBLE[slot]
    return player_pos == slot


def best_bench_swap(storage: Storage, roster_positions: list[str], row: dict) -> dict | None:
    """Largest legal single benched-over-started improvement for one team-week.
    Returns {benched, started, slot, delta, benched_points, started_points}."""
    starters = row.get("starters") or []
    players_points = row.get("players_points") or {}
    if not starters or not players_points:
        return None
    layout = _slot_layout(roster_positions)
    benched_ids = [pid for pid in players_points if pid not in starters]
    best = None
    for i, sid in enumerate(starters):
        slot = layout[i] if i < len(layout) else "FLEX"
        # A benched kicker outscoring the started one is not a decision
        # anybody made. You start your only kicker, and shaming a manager
        # for it is the calibration error this codebase already refuses
        # everywhere else.
        if slot in SPECIAL_TEAMS:
            continue
        s_pts = float(players_points.get(sid, 0))
        s_player = storage.get_player(sid) or {}
        for bid in benched_ids:
            b_player = storage.get_player(bid) or {}
            if not _eligible(b_player.get("position") or "?", slot):
                continue
            b_pts = float(players_points.get(bid, 0))
            delta = b_pts - s_pts
            if best is None or delta > best["delta"]:
                best = {
                    "slot": slot,
                    "started": s_player.get("full_name") or sid,
                    "started_points": s_pts,
                    "benched": b_player.get("full_name") or bid,
                    "benched_points": b_pts,
                    "delta": round(delta, 1),
                }
    return best


def _week_rows(analysis: dict) -> list[dict]:
    """Flatten matchups into per-team rows with opponent context."""
    rows = []
    for m in analysis["matchups"]:
        a, b = m["teams"]
        for me, opp in ((a, b), (b, a)):
            if me["points"] is None:
                continue
            rows.append({
                "matchup": m, "team": me, "opponent": opp,
                "won": me["points"] > opp["points"],
                "margin": round(me["points"] - opp["points"], 2),
            })
    return rows


def weekly_award_nominations(
    storage: Storage,
    league: League,
    week: int,
    *,
    analysis: dict | None = None,
    preseason_ranks: dict[int, int] | None = None,
) -> list[dict]:
    analysis = analysis or analyze_week(storage, league, week)
    if not analysis:
        return []
    league_data = storage.get_league(league.league_id) or {}
    roster_positions = league_data.get("roster_positions") or []
    faab_budget = (league_data.get("settings") or {}).get("waiver_budget") or 100
    rows = _week_rows(analysis)
    if not rows:
        return []  # nothing played: no award has a legitimate nominee
    season = analysis["season"]
    scores = sorted((r["team"]["points"] for r in rows), reverse=True)
    awards: list[dict] = []

    def matchup_ev(r):
        return [f"sleeper:matchup:{league.league_id}:{week}:{r['matchup']['matchup_id']}",
                evidence.roster_ref(league.league_id, r["team"]["roster_id"])]

    # --- Shame! Shame! Shame! ---
    shame = []
    week_rows_by_roster = {row["roster_id"]: row for row in storage.get_matchups(league.league_id, week)}
    for r in rows:
        raw = week_rows_by_roster.get(r["team"]["roster_id"])
        swap = best_bench_swap(storage, roster_positions, raw or {})
        if swap and swap["delta"] >= SHAME_MIN_DELTA:
            outcome_changing = (not r["won"]) and swap["delta"] > abs(r["margin"])
            shame.append({
                "team_slug": r["team"]["team_slug"],
                "roster_id": r["team"]["roster_id"],
                "metric_value": swap["delta"],
                "outcome_changing": outcome_changing,
                "facts": [
                    f"Started {swap['started']} ({swap['started_points']:g}) at {swap['slot']}; "
                    f"benched {swap['benched']} ({swap['benched_points']:g}).",
                    f"Points sacrificed: {swap['delta']:g}. Final margin: {r['margin']:+g}. "
                    f"Outcome-changing: {'yes' if outcome_changing else 'no'}.",
                    "Basis: largest legal single bench-over-starter swap; hindsight, "
                    "which is stated, not equated with bad process.",
                ],
                "evidence": matchup_ev(r),
            })
    shame.sort(key=lambda n: (-n["outcome_changing"], -n["metric_value"]))
    awards.append({
        "award_key": "shame", "award_name": "Shame! Shame! Shame!",
        "kind": "objective-evidence",
        "metric": f"Bench-over-starter points sacrificed (min {SHAME_MIN_DELTA:g}); "
                  "outcome-changing flagged when the sacrifice exceeded the losing margin.",
        "nominees": shame[:4],
    })

    # --- Hard-Luck Bastard: strong week, lost anyway ---
    hard_luck = []
    for r in rows:
        if r["won"]:
            continue
        score_rank = scores.index(r["team"]["points"]) + 1
        ap = r["team"].get("all_play")
        if score_rank <= 3 or (ap and ap.get("pct") and ap["pct"] >= 0.7):
            hard_luck.append({
                "team_slug": r["team"]["team_slug"], "metric_value": r["team"]["points"],
                "facts": [f"Lost by {abs(r['margin']):g} with {r['team']['points']:g} points "
                          f"(#{score_rank} score of the week)."],
                "evidence": matchup_ev(r),
            })
    hard_luck.sort(key=lambda n: -n["metric_value"])
    awards.append({
        "award_key": "hard-luck-bastard", "award_name": "Hard-Luck Bastard",
        "kind": "objective",
        "metric": "A top-3 weekly score (or ≥.700 all-play) that still lost.",
        "nominees": hard_luck[:3],
    })

    # --- Escape Artist: won ugly ---
    escape = []
    for r in rows:
        if not r["won"]:
            continue
        score_rank = scores.index(r["team"]["points"]) + 1
        eff = (r["team"].get("lineup_efficiency") or {}).get("efficiency")
        if score_rank > len(rows) - 3 or (eff is not None and eff < 0.75):
            facts = [f"Won by {r['margin']:g} with {r['team']['points']:g} points "
                     f"(#{score_rank} of {len(rows)} scores this week)."]
            if eff is not None:
                facts.append(f"Lineup efficiency {eff:.0%} of optimal.")
            escape.append({"team_slug": r["team"]["team_slug"], "metric_value": score_rank,
                           "facts": facts, "evidence": matchup_ev(r)})
    escape.sort(key=lambda n: -n["metric_value"])
    awards.append({
        "award_key": "escape-artist", "award_name": "Escape Artist",
        "kind": "objective",
        "metric": "A win despite a bottom-3 weekly score or sub-75% lineup efficiency.",
        "nominees": escape[:3],
    })

    # --- Mercy Rule: biggest blowout ---
    blowouts = sorted((r for r in rows if r["won"]), key=lambda r: -r["margin"])
    awards.append({
        "award_key": "mercy-rule", "award_name": "Mercy Rule",
        "kind": "objective",
        "metric": f"Largest margin of the week (strong candidate at {MERCY_STRONG_MARGIN:g}+).",
        "nominees": [
            {"team_slug": r["team"]["team_slug"], "metric_value": r["margin"],
             "facts": [f"Beat {r['opponent']['team_slug']} {r['team']['points']:g} to "
                       f"{r['opponent']['points']:g} (margin {r['margin']:g})."],
             "evidence": matchup_ev(r)}
            for r in blowouts[:2] if r["margin"] >= 20
        ],
    })

    # --- Upset of the Week: labeled non-projection basis ---
    upsets = []
    for r in rows:
        if not r["won"]:
            continue
        gap = r["team"]["standing"] - r["opponent"]["standing"]
        basis = None
        if analysis.get("weeks_played", 0) >= 2 and gap >= UPSET_MIN_STANDINGS_GAP:
            basis = (f"pre-week standings differential: #{r['team']['standing']} beat "
                     f"#{r['opponent']['standing']}")
        elif preseason_ranks:
            pr_me = preseason_ranks.get(r["team"]["roster_id"])
            pr_opp = preseason_ranks.get(r["opponent"]["roster_id"])
            if pr_me and pr_opp and pr_me - pr_opp >= UPSET_MIN_STANDINGS_GAP:
                basis = (f"commissioner's preseason Peer and Near-Peer ranking: "
                         f"#{pr_me} beat #{pr_opp}")
        if basis:
            upsets.append({
                "team_slug": r["team"]["team_slug"], "metric_value": gap,
                "facts": [f"Basis: {basis}. No projection source exists; no projection-based "
                          "upset metric is fabricated."],
                "evidence": matchup_ev(r),
            })
    upsets.sort(key=lambda n: -n["metric_value"])
    awards.append({
        "award_key": "upset-of-the-week", "award_name": "Upset of the Week",
        "kind": "objective-labeled-basis",
        "metric": "Underdog win; basis labeled per nominee (standings or commissioner "
                  "preseason ranking — never fabricated projections).",
        "nominees": upsets[:3],
    })

    # --- transactions: Waiver Wire Heist / FAAB Arsonist ---
    heists, arsons = [], []
    for w in range(max(1, week - 1), week + 1):
        for t in storage.get_transactions(league.league_id, w):
            if t.get("status") != "complete" or t.get("type") not in ("waiver", "free_agent"):
                continue
            faab = faab_cost(t)
            for pid, rid in (t.get("adds") or {}).items():
                raw = week_rows_by_roster.get(rid)
                if not raw:
                    continue
                pts = float((raw.get("players_points") or {}).get(pid, 0))
                started = pid in (raw.get("starters") or [])
                player = (storage.get_player(pid) or {}).get("full_name") or pid
                team = next((r["team"]["team_slug"] for r in rows
                             if r["team"]["roster_id"] == rid), f"roster-{rid}")
                ev = [f"sleeper:transaction:{t.get('transaction_id')}",
                      evidence.roster_ref(league.league_id, rid)]
                if started and pts >= HEIST_MIN_POINTS:
                    heists.append({"team_slug": team, "player": player, "metric_value": pts,
                                   "facts": [f"Added week {w}"
                                             + (f" for {faab} FAAB" if faab else "")
                                             + f", started, scored {pts:g}."],
                                   "evidence": ev})
                if faab >= FAAB_ARSON_PCT * faab_budget and (not started or pts <= 5):
                    arsons.append({"team_slug": team, "player": player, "metric_value": faab,
                                   "facts": [f"Spent {faab} of {faab_budget} FAAB "
                                             f"({faab / faab_budget:.0%}); "
                                             + ("did not start him." if not started
                                                else f"started him for {pts:g}.")],
                                   "evidence": ev})
    heists.sort(key=lambda n: -n["metric_value"])
    arsons.sort(key=lambda n: -n["metric_value"])
    awards.append({"award_key": "waiver-wire-heist", "award_name": "Waiver Wire Heist",
                   "kind": "objective",
                   "metric": f"Waiver/FA addition started immediately for {HEIST_MIN_POINTS:g}+ points.",
                   "nominees": heists[:3]})
    awards.append({"award_key": "faab-arsonist", "award_name": "FAAB Arsonist",
                   "kind": "objective",
                   "metric": f"≥{FAAB_ARSON_PCT:.0%} of FAAB budget spent on a player who "
                             "sat or flopped that week.",
                   "nominees": arsons[:3]})

    # --- Benchwarmer Memorial ---
    memorials = []
    for r in rows:
        raw = week_rows_by_roster.get(r["team"]["roster_id"]) or {}
        starters = raw.get("starters") or []
        for pid, pts in (raw.get("players_points") or {}).items():
            if pid in starters or float(pts) < BENCH_MEMORIAL_MIN:
                continue
            player = (storage.get_player(pid) or {}).get("full_name") or pid
            memorials.append({"team_slug": r["team"]["team_slug"], "player": player,
                              "metric_value": float(pts),
                              "facts": [f"{player} scored {float(pts):g} on the bench."],
                              "evidence": matchup_ev(r)})
    memorials.sort(key=lambda n: -n["metric_value"])
    awards.append({"award_key": "benchwarmer-memorial", "award_name": "Benchwarmer Memorial",
                   "kind": "objective",
                   "metric": f"Highest benched score of the week (min {BENCH_MEMORIAL_MIN:g}).",
                   "nominees": memorials[:3]})

    # --- Manager of the Week (subjective; evidence, no verdict) ---
    motw = []
    for r in rows:
        if not r["won"]:
            continue
        hooks, ev = [], matchup_ev(r)
        eff = (r["team"].get("lineup_efficiency") or {}).get("efficiency")
        if eff is not None and eff >= 0.95:
            hooks.append(f"lineup efficiency {eff:.0%} of optimal")
        if any(h["team_slug"] == r["team"]["team_slug"] for h in heists):
            hooks.append("a waiver addition contributed immediately")
        ap = r["team"].get("all_play")
        if ap and ap.get("pct") is not None and ap["pct"] >= 0.8:
            hooks.append(f"all-play {ap['wins']}-{ap['losses']}")
        if any(u["team_slug"] == r["team"]["team_slug"] for u in upsets):
            hooks.append("won as the labeled underdog")
        if hooks:
            motw.append({"team_slug": r["team"]["team_slug"], "facts":
                         [f"Won by {r['margin']:g}; " + "; ".join(hooks) + "."],
                         "evidence": ev})
    awards.append({"award_key": "manager-of-the-week", "award_name": "Manager of the Week",
                   "kind": "subjective",
                   "metric": "No formula — nominees show management evidence (efficiency, "
                             "moves, all-play, underdog result); raw high score alone does "
                             "not nominate.",
                   "nominees": motw[:4]})

    # --- Galaxy Brain (subjective; contrarian start that hit) ---
    galaxy = []
    for r in rows:
        raw = week_rows_by_roster.get(r["team"]["roster_id"]) or {}
        starters = raw.get("starters") or []
        pp = raw.get("players_points") or {}
        for sid in starters:
            s_pts = float(pp.get(sid, 0))
            if s_pts < 18:
                continue
            s_player = storage.get_player(sid) or {}
            s_rank = s_player.get("search_rank") or 10**6
            beaten = None
            for bid in pp:
                if bid in starters:
                    continue
                b_player = storage.get_player(bid) or {}
                b_rank = b_player.get("search_rank") or 10**6
                b_pts = float(pp.get(bid, 0))
                if (b_player.get("position") == s_player.get("position")
                        and b_rank < s_rank and b_pts < s_pts - 10):
                    beaten = (b_player.get("full_name") or bid, b_pts)
                    break
            if beaten:
                galaxy.append({
                    "team_slug": r["team"]["team_slug"],
                    "player": s_player.get("full_name") or sid,
                    "metric_value": s_pts,
                    "facts": [f"Started {s_player.get('full_name')} ({s_pts:g}) over the "
                              f"bigger name on the bench ({beaten[0]}, {beaten[1]:g}). "
                              "Basis: Sleeper search rank as the name-brand proxy, labeled as such."],
                    "evidence": matchup_ev(r),
                })
    galaxy.sort(key=lambda n: -n["metric_value"])
    awards.append({"award_key": "galaxy-brain", "award_name": "Galaxy Brain",
                   "kind": "subjective",
                   "metric": "A contrarian start that hit (started over a bigger name who "
                             "sat and scored less); subjective call on whether it was brains "
                             "or luck.",
                   "nominees": galaxy[:3]})

    # slate labels
    for aw in awards:
        if not aw["nominees"]:
            aw["slate"] = "none"
        else:
            top = aw["nominees"][0]
            strong = (
                (aw["award_key"] == "shame" and top.get("outcome_changing"))
                or (aw["award_key"] == "mercy-rule" and top.get("metric_value", 0) >= MERCY_STRONG_MARGIN)
                or (aw["award_key"] in ("hard-luck-bastard", "waiver-wire-heist",
                                        "benchwarmer-memorial", "escape-artist")
                    and len(aw["nominees"]) >= 1)
            )
            aw["slate"] = "strong" if strong else "possible"
    return awards
