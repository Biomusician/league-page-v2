"""Who is actually good, and who has had a nice schedule.

The standings page already prints all-play and lineup efficiency in a sortable
table. A table is not an argument. The number a league argues about is the gap
between the record a team has and the record its scoring earned, and nobody
computes that in their head from two columns.

So this states it: how many games ahead of or behind its own scoring each team
is running, in games, with the denominator named.

Two honesty constraints shape the whole module.

**The extremes are structurally rigged.** An undefeated team CANNOT have a
record below its all-play unless its all-play is also perfect, so a naive
"outperforming" test nominates every unbeaten team and every winless one by
arithmetic rather than by luck. A 5-0 team on a .700 all-play really is a
little ahead of its scoring, but it is also the best scoring team in the
league, and a line that calls it lucky is wrong about the season. Those rows
get a different sentence.

**All-play denominators are not equal.** A team with a bye inside the window
played fewer all-play games than the league, so its percentage is over a
smaller denominator. `all_play` reports `games` and `weeks` now, and a row
whose sample is short of the league's says so rather than being ranked
silently against a longer one.
"""
from __future__ import annotations

from leaguepage.config import League
from leaguepage.matchup_analysis import all_play
from leaguepage.storage import Storage
from leaguepage.team_analytics import team_record, weekly_scores

# Below this the gap is noise: one bad Sunday moves it by a whole game.
MIN_WEEKS = 4
# Under half a game of difference is not a story anybody would tell.
MIN_GAP_GAMES = 0.8


def _phrase(gap: float) -> str:
    """A gap in games, said the way it would be said out loud."""
    n = abs(gap)
    if n < 1.5:
        return "about a game"
    if n < 2.5:
        return "two games"
    if n < 3.5:
        return "three games"
    return f"{n:.0f} games"


def reality_check(storage: Storage, league: League, through_week: int,
                  names: dict[int, str], slugs: dict[int, str]) -> dict | None:
    """Per-team luck gap, plus the two ends of it.

    Returns None until the season has enough weeks for the number to mean
    anything. Every row carries the denominator it was computed over.
    """
    scores = weekly_scores(storage, league.league_id, through_week)
    if not scores:
        return None
    weeks = max((len(v) for v in scores.values()), default=0)
    if weeks < MIN_WEEKS:
        return None

    ap = all_play(scores)
    league_weeks = max((r.get("weeks", 0) for r in ap.values()), default=0)
    rows = []
    for r in storage.get_rosters(league.league_id):
        rid = r["roster_id"]
        rec, a = team_record(r), ap.get(rid)
        if not a or not a.get("games"):
            continue
        played = rec["wins"] + rec["losses"] + rec.get("ties", 0)
        if not played:
            continue
        win_pct = (rec["wins"] + 0.5 * rec.get("ties", 0)) / played
        # What the record would be if every week's score had been played
        # against the whole league instead of against one opponent.
        earned_wins = a["pct"] * played
        gap = rec["wins"] - earned_wins
        rows.append({
            "roster_id": rid,
            "name": names.get(rid) or f"Roster {rid}",
            "slug": slugs.get(rid),
            "record": f"{rec['wins']}-{rec['losses']}"
                      + (f"-{rec['ties']}" if rec.get("ties") else ""),
            "all_play": f"{a['wins']}-{a['losses']}",
            "all_play_pct": a["pct"],
            "win_pct": round(win_pct, 3),
            "gap": round(gap, 1),
            "games": a["games"],
            "weeks": a.get("weeks", 0),
            # A team whose window is shorter than the league's is being
            # compared over a different denominator, and should say so.
            "short_sample": a.get("weeks", 0) < league_weeks,
            "line": _line(rec, a, gap, played),
        })
    rows.sort(key=lambda r: -r["gap"])
    material = [r for r in rows if abs(r["gap"]) >= MIN_GAP_GAMES]
    return {
        "weeks": weeks,
        "rows": rows,
        "luckiest": material[0] if material else None,
        "unluckiest": material[-1] if len(material) > 1 else None,
        # The league's full window, not the first row's: a team with a bye
        # played fewer, and quoting its count as the league's understates
        # the denominator on the very line that exists to state it.
        "note": (f"All-play plays every team against the whole league every "
                 f"week. Through {weeks} week{'' if weeks == 1 else 's'}, "
                 f"that is {max(r['games'] for r in rows)} games per team "
                 f"instead of {weeks}." if rows else ""),
    }


def _line(rec: dict, a: dict, gap: float, played: int) -> str:
    """One sentence about this team's gap, or nothing.

    The unbeaten and winless cases get their own wording because the test
    cannot fail for them: a team that has won every game is above its own
    all-play by arithmetic unless its all-play is also perfect.
    """
    ap_rank_pct = a["pct"]
    if abs(gap) < MIN_GAP_GAMES:
        return "The record and the scoring agree."
    if rec["losses"] == 0 and played:
        if ap_rank_pct >= 0.75:
            return ("Unbeaten and scoring like it. The all-play says this is "
                    "real.")
        return (f"Unbeaten on a {ap_rank_pct:.0%} all-play. Every team that "
                f"wins them all is ahead of its scoring; this one is ahead by "
                f"{_phrase(gap)}.")
    if rec["wins"] == 0 and played:
        if ap_rank_pct <= 0.25:
            return "Winless, and the scoring agrees. This is not bad luck."
        return (f"Winless on a {ap_rank_pct:.0%} all-play, which is "
                f"{_phrase(gap)} worse than the scoring earned.")
    if gap > 0:
        return (f"{_phrase(gap).capitalize()} better than the scoring earned. "
                f"{a['wins']}-{a['losses']} against the whole league.")
    return (f"{_phrase(gap).capitalize()} worse than the scoring earned. "
            f"{a['wins']}-{a['losses']} against the whole league.")
