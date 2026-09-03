"""The five numbers from a week that anybody would repeat out loud.

Three hundred and eighty-seven lines of award machinery sat in this
codebase, imported by the Desk and by nothing else. Ten awards, thresholds,
evidence arrays. None of it reached a reader.

It cannot reach a reader as awards. Award winners are the Commissioner's to
pick, and a build step that quietly hands out Manager of the Week has taken
his job. But most of what that engine computes is not a verdict at all — it
is a fact with a name attached. The highest score of the week is not an
opinion. Neither is the biggest margin, the closest game, the most points
somebody left on his bench, or the best thing anybody pulled off waivers.

So this ships the facts and leaves the awards alone. Each row is a
superlative, a team, a number, and the evidence it came from. Nothing here
declares a winner of anything, and the private nomination slate the Desk
reads is untouched.

The framing carries as much of the weight as the content. "Highest score"
beats "Hard-Luck Bastard nominee (slate: possible)" for the same underlying
number, because one of them is something a reader would repeat and the other
is a note to an editor.
"""
from __future__ import annotations

from leaguepage import evidence
from leaguepage.config import League
from leaguepage.draft_value import SPECIAL_TEAMS
from leaguepage.matchup_analysis import analyze_week, faab_cost
from leaguepage.storage import Storage
from leaguepage.weekly_awards import _week_rows

# A bench total below this is a normal week, not a story.
BENCH_FLOOR = 20.0
# A waiver add has to have actually done something.
WAIVER_FLOOR = 12.0


def week_leaders(storage: Storage, league: League, week: int,
                 names: dict[int, str], slugs: dict[int, str],
                 *, analysis: dict | None = None) -> list[dict]:
    """Zero to five rows, in the order a reader would care about them.

    Every row carries `evidence`, so nothing here is a number without a
    provenance trail back to the payload it came from.
    """
    analysis = analysis or analyze_week(storage, league, week)
    if not analysis:
        return []
    rows = _week_rows(analysis)
    if not rows:
        return []

    def _name(rid):
        return names.get(rid) or f"Roster {rid}"

    def _ev(row):
        return [f"sleeper:matchup:{league.league_id}:{week}:"
                f"{row['matchup']['matchup_id']}",
                evidence.roster_ref(league.league_id, row["team"]["roster_id"])]

    def out_row(label, rid, value, detail, ev):
        return {"label": label, "team": _name(rid), "slug": slugs.get(rid),
                "value": value, "detail": detail, "evidence": ev}

    out: list[dict] = []

    # --- highest score ---
    top = max(rows, key=lambda r: r["team"]["points"])
    out.append(out_row("Highest score", top["team"]["roster_id"],
                       f"{top['team']['points']:g}",
                       f"the week's best total, out of {len(rows)} teams",
                       _ev(top)))

    # --- biggest margin, and the closest game ---
    wins = [r for r in rows if r["won"]]
    if wins:
        widest = max(wins, key=lambda r: r["margin"])
        out.append(out_row("Biggest margin", widest["team"]["roster_id"],
                           f"{widest['margin']:g}",
                           f"over {_name(widest['opponent']['roster_id'])}",
                           _ev(widest)))
        tightest = min(wins, key=lambda r: r["margin"])
        # A blowout and a nailbiter cannot be the same game.
        if tightest["matchup"] is not widest["matchup"]:
            out.append(out_row("Closest game", tightest["team"]["roster_id"],
                               f"{tightest['margin']:g}",
                               f"survived {_name(tightest['opponent']['roster_id'])}",
                               _ev(tightest)))

    # --- most left on the bench ---
    # Kickers and defenses are excluded for the reason the rest of this
    # codebase excludes them: you start your only kicker, so a benched one
    # outscoring him is not a decision anybody made.
    by_roster = {r["roster_id"]: r
                 for r in storage.get_matchups(league.league_id, week)}
    best_bench = None
    for r in rows:
        raw = by_roster.get(r["team"]["roster_id"]) or {}
        starters = raw.get("starters") or []
        for pid, pts in (raw.get("players_points") or {}).items():
            if pid in starters or float(pts) < BENCH_FLOOR:
                continue
            info = storage.get_player(pid) or {}
            if (info.get("position") or "").upper() in SPECIAL_TEAMS:
                continue
            cand = (float(pts), r, info.get("full_name") or pid)
            if best_bench is None or cand[0] > best_bench[0]:
                best_bench = cand
    if best_bench:
        pts, r, player = best_bench
        out.append(out_row("Most left on the bench", r["team"]["roster_id"],
                           f"{pts:g}", f"{player} scored it without starting",
                           _ev(r)))

    # --- best thing off waivers ---
    row_by_rid = {r["team"]["roster_id"]: r for r in rows}
    best_add = None
    for tx in storage.get_transactions(league.league_id, week) or []:
        if tx.get("status") != "complete" or tx.get("type") == "trade":
            continue
        for pid, rid in (tx.get("adds") or {}).items():
            r = row_by_rid.get(rid)
            if not r:
                continue
            raw = by_roster.get(rid) or {}
            pts = float((raw.get("players_points") or {}).get(pid, 0))
            if pts < WAIVER_FLOOR:
                continue
            info = storage.get_player(pid) or {}
            cand = (pts, rid, info.get("full_name") or pid, faab_cost(tx),
                    [f"sleeper:transaction:{tx.get('transaction_id')}",
                     evidence.roster_ref(league.league_id, rid)])
            if best_add is None or cand[0] > best_add[0]:
                best_add = cand
    if best_add:
        pts, rid, player, cost, ev = best_add
        out.append(out_row(
            "Best pickup", rid, f"{pts:g}",
            f"{player}, added this week"
            + (f" for {cost} FAAB" if cost else " for nothing"), ev))

    return out
