"""What a writer actually needs to know before writing a matchup.

The old brief answered "why does the system think this matchup is
interesting". That is a scoring question, and it had already been answered
by the time he opened the card. The questions he was still looking things
up for by hand were these:

  who actually decides this game        key_players
  who might not play                    availability
  what does each side have to overcome  gap_to_close
  what did they just do                 recent_moves
  what could they still do              possible_moves
  what did they do to themselves        self_inflicted
  have these two done anything before   notable_callback

Every function returns lines with their basis attached, and says what it
cannot know rather than filling the space. In week 1 that is most of it:
no games have been played, so there are no scores, no records and no
history, and a brief that implied otherwise would be worse than a short
one. Sleeper's public API publishes no projections and the synced player
payload carries no bye weeks, so neither is ever inferred.

All of this is PRIVATE. It is research for the Commissioner's eyes; none
of it reaches a reader unless he writes it into his own prose.
"""
from __future__ import annotations

from leaguepage.config import League
from leaguepage.storage import Storage

# Sleeper reports these on a player; anything else means available.
OUT_STATUSES = {"Out", "IR", "PUP", "Sus", "NA", "Doubtful"}
RISK_STATUSES = {"Questionable", "Probable"}

# A prior meeting has to have been worth remembering. These are the reasons
# a reader would remember one; a routine win is not one of them.
CLOSE_MARGIN = 5.0        # decided by less than a field goal's worth
BLOWOUT_MARGIN = 40.0
KEY_PLAYERS = 4


def _name(resolved: dict, rid: int) -> str:
    return (resolved.get(rid) or {}).get("name") or f"Roster {rid}"


def _roster(storage: Storage, league: League, rid: int) -> dict:
    for r in storage.get_rosters(league.league_id):
        if r["roster_id"] == rid:
            return r
    return {}


# ------------------------------------------------------------ key players

def key_players(storage: Storage, league: League, team: dict, values: dict,
                stage: str, *, limit: int = KEY_PLAYERS) -> list[str]:
    """The starters most likely to decide it, and what that ranking rests on.

    `stage` comes from team_analytics.player_values and is the honest label
    for the basis: before there are games it is consensus draft rank, after
    which real scoring blends in. It is printed, because "key player" from
    a preseason board and "key player" from six weeks of scoring are two
    different claims.
    """
    starters = [p for p in (team.get("starters") or []) if p and p != "0"]
    ranked = sorted(
        ({"pid": pid, **values[pid]} for pid in starters if pid in values),
        key=lambda p: -p["value"])
    if not ranked:
        return []
    out = [f"  basis: {stage}"]
    for p in ranked[:limit]:
        out.append(f"  {p['position']} {p['name']}")
    return out


# ------------------------------------------------------------ availability

def availability(storage: Storage, league: League, team: dict, *,
                 season: str | int | None = None, week: int | None = None) -> list[str]:
    """Who is carrying a designation, on the starting lineup first.

    Byes come from the reference schedule when one is on file for the
    season (`nfl_schedule`); a starter whose NFL team has no game that week
    is listed first, because he is the one certain absence. Without a
    schedule the note below says so rather than guessing.
    """
    from leaguepage.nfl_schedule import teams_on_bye

    starters = {p for p in (team.get("starters") or []) if p and p != "0"}
    rid = team["roster_id"]
    roster = _roster(storage, league, rid)
    byes = teams_on_bye(season, week) if (season is not None and week) else None
    flagged = []
    for pid in (roster.get("players") or []):
        p = storage.get_player(pid) or {}
        name = p.get("full_name") or pid
        where = "STARTING" if pid in starters else "bench"
        if byes and (p.get("team") or "") in byes:
            flagged.append((pid in starters, "",
                            f"  BYE — {p.get('position', '?')} {name} ({where}, "
                            f"{p.get('team')} does not play this week)"))
        status = (p.get("injury_status") or "").strip()
        if not status:
            continue
        detail = (p.get("injury_body_part") or "").strip()
        flagged.append((pid in starters, status,
                        f"  {status.upper()} — {p.get('position', '?')} {name} "
                        f"({where}{', ' + detail if detail else ''})"))
    if not flagged:
        return ["  nobody on this roster carries an injury designation"
                + (" or a bye" if byes is not None else "")]
    flagged.sort(key=lambda f: (not f[0], f[1]))
    return [f[2] for f in flagged]


def bye_note(season: str | int | None = None, week: int | None = None) -> str:
    """What the schedule can and cannot say about byes this week."""
    from leaguepage.nfl_schedule import describe_source, teams_on_bye

    byes = teams_on_bye(season, week) if (season is not None and week) else None
    if byes is None:
        return ("  byes: not available — no NFL schedule on file for this "
                "season. Check Sleeper before writing one.")
    src = describe_source(season) or "reference schedule"
    if not byes:
        return f"  byes: none this week ({src})"
    return f"  byes this week: {', '.join(sorted(byes))} ({src})"


# ------------------------------------------------------------ gap to close

def gap_to_close(profile: dict, team: dict, other: dict, name: str,
                 other_name: str, *, weeks_played: int) -> list[str]:
    """What this side has to overcome, from this side's point of view.

    Written for both teams, separately, because "the gap" as a single
    number is the losing team's problem stated once. A preview has two
    subjects and each of them is behind in something.
    """
    from leaguepage.team_analytics import is_rated, label_for_rank

    out = []
    rec, orec = team.get("record") or {}, other.get("record") or {}
    if weeks_played:
        w, l = rec.get("wins", 0), rec.get("losses", 0)
        ow, ol = orec.get("wins", 0), orec.get("losses", 0)
        out.append(f"  record {w}-{l} against {ow}-{ol}")
        if team.get("points") is not None and other.get("points") is not None:
            out.append(f"  season points {team['points']:.1f} to {other['points']:.1f}")
        if team.get("standing") and other.get("standing"):
            out.append(f"  standing #{team['standing']} to #{other['standing']}")
    else:
        out.append("  no games played yet: nothing to close on the table")

    behind = []
    for pos, ranks in (profile.get("ranks") or {}).items():
        mine, theirs = ranks.get(team["roster_id"]), ranks.get(other["roster_id"])
        if mine is None or theirs is None or mine <= theirs:
            continue
        if not (is_rated(profile, pos, team["roster_id"])
                and is_rated(profile, pos, other["roster_id"])):
            continue
        behind.append((mine - theirs, pos, mine, theirs))
    behind.sort(reverse=True)
    n = profile.get("n") or len(profile.get("teams") or []) or 0
    for _d, pos, mine, theirs in behind[:3]:
        out.append(f"  behind at {pos}: {label_for_rank(mine, n)} to "
                   f"{other_name}'s {label_for_rank(theirs, n)}")
    if not behind:
        out.append(f"  not behind {other_name} at any rated position")
    return out


# ------------------------------------------------------------ the lineup

# A bench player has to out-rate the starter by this much (in value points,
# roughly reference-rank places) before the brief calls it a decision. Two
# players ten places apart on a preseason board are a coin flip, not a call.
LINEUP_CALL_MARGIN = 15.0


def league_starting_values(storage: Storage, league: League, week: int,
                           values: dict) -> dict[str, list[float]]:
    """Every starter's value this week, by position, across the league.

    What "a weak starting TE" is measured against: the other nine or eleven
    starting tight ends in this league this week, not a national number.
    """
    out: dict[str, list[float]] = {}
    for row in storage.get_matchups(league.league_id, week):
        for pid in (row.get("starters") or []):
            v = values.get(pid)
            if not v or not v.get("position"):
                continue
            out.setdefault(v["position"], []).append(float(v.get("value") or 0.0))
    return out


def _median(xs: list[float]) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def weakest_slot(team: dict, values: dict, league_starters: dict[str, list[float]],
                 stage: str) -> list[str]:
    """The starter furthest below what this league starts at his position.

    Written per team because the slot that loses a matchup is usually the
    one nobody wrote about. The basis is printed: off a preseason board it
    is a claim about draft rank, and after games it is a claim about
    scoring, and those are different sentences.
    """
    starters = [p for p in (team.get("starters") or []) if p and p != "0"]
    worst = None
    for pid in starters:
        v = values.get(pid)
        if not v or not v.get("position"):
            continue
        med = _median(league_starters.get(v["position"]) or [])
        if med is None:
            continue
        gap = float(v.get("value") or 0.0) - med
        if worst is None or gap < worst[0]:
            worst = (gap, v)
    if worst is None:
        return []
    gap, v = worst
    if gap >= 0:
        return [f"  no starting slot below the league's median starter ({stage})"]
    return [f"  {v['position']} {v['name']}: {abs(gap):.0f} points under the league's "
            f"median starting {v['position']} ({stage})"]


def lineup_calls(storage: Storage, league: League, team: dict, values: dict,
                 *, limit: int = 2) -> list[str]:
    """Bench players who out-rate a starter at their own position.

    A decision waiting to be made, or already made and worth asking about.
    Same position only: whether a bench WR should displace a starting RB in
    a flex is a projection question, and there is no projection here.
    """
    starters = [p for p in (team.get("starters") or []) if p and p != "0"]
    roster = _roster(storage, league, team["roster_id"])
    bench = [p for p in (roster.get("players") or []) if p not in starters]
    weakest: dict[str, dict] = {}
    for pid in starters:
        v = values.get(pid)
        if not v or not v.get("position"):
            continue
        cur = weakest.get(v["position"])
        if cur is None or float(v.get("value") or 0) < float(cur.get("value") or 0):
            weakest[v["position"]] = v
    calls = []
    for pid in bench:
        v = values.get(pid)
        if not v or not v.get("position") or v["position"] not in weakest:
            continue
        w = weakest[v["position"]]
        edge = float(v.get("value") or 0) - float(w.get("value") or 0)
        if edge >= LINEUP_CALL_MARGIN:
            calls.append((edge, f"  bench {v['position']} {v['name']} rates {edge:.0f} "
                                f"above starter {w['name']} (reference rank, not a projection)"))
    calls.sort(key=lambda c: -c[0])
    return [c[1] for c in calls[:limit]]


# ------------------------------------------------------------ construction

# Draft construction is a story while the season is young. Once results
# exist they say more than the draft did, and the block goes quiet.
CONSTRUCTION_WEEKS = 4


def how_built(storage: Storage, league: League, team: dict, *,
              weeks_played: int) -> list[str]:
    """How this roster was assembled: the shape of the first rounds and the
    one pick furthest from the reference board in each direction."""
    if weeks_played >= CONSTRUCTION_WEEKS:
        return []
    from leaguepage.adp import load_adp_for_league
    from leaguepage.draft_analysis import analyze_league_draft
    from leaguepage.draft_value import (CLASS_REACH, CLASS_STEAL, SKILL_POSITIONS,
                                        classify_pick)

    rid = team["roster_id"]
    analysis = analyze_league_draft(storage, league, adp=load_adp_for_league(league))
    if not analysis:
        return []
    size = analysis.get("total_teams") or 0
    for t in analysis.get("teams", []):
        if t.get("roster_id") != rid:
            continue
        picks = t.get("picks_by_round") or []
        out = []
        first = [p.get("position") for p in picks[:3] if p.get("position")]
        if first:
            out.append(f"  opened {', '.join(first)}")
        rated = [(p, classify_pick(p["delta"], size, off_board=p.get("off_board", False)))
                 for p in picks if p.get("delta") is not None
                 and p.get("position") in SKILL_POSITIONS]
        reach = min((pc for pc in rated if pc[1] and pc[1]["draft_value_class"] == CLASS_REACH),
                    key=lambda pc: pc[0]["delta"], default=None)
        steal = max((pc for pc in rated if pc[1] and pc[1]["draft_value_class"] == CLASS_STEAL),
                    key=lambda pc: pc[0]["delta"], default=None)
        if reach:
            out.append(f"  biggest reach: {reach[0]['name']} at {reach[0]['pick_no']}, "
                       f"{reach[1]['label'].removeprefix('REACH · ')}")
        if steal:
            out.append(f"  biggest steal: {steal[0]['name']} at {steal[0]['pick_no']}, "
                       f"{steal[1]['label'].removeprefix('STEAL · ')}")
        return out
    return []


# ------------------------------------------------------------ on the record

def on_the_record(storage: Storage, league: League, season: str, week: int,
                  rids: list[int], names: dict[int, str], slugs: dict[int, str],
                  *, limit: int = 3) -> list[str]:
    """Open takes and live receipts touching either side of this matchup.

    The paper remembering itself. Nothing here is recorded as shown, so
    the brief can render as often as it likes without spending a callback.
    """
    from leaguepage import takes as takes_mod

    out = []
    mine = {slugs.get(rid) for rid in rids} | {str(rid) for rid in rids}
    for t in storage.open_takes(league.slug, season):
        subj = t.get("subject") or ""
        if subj not in mine and not any(slugs.get(rid) and slugs[rid] in subj for rid in rids):
            continue
        rec = t.get("recommended_status")
        label = takes_mod.STATUS_LABELS.get(rec, rec) if rec else "no reading yet"
        out.append(f"  take {t['take_id']} ({t.get('context') or 'undated'}, engine: {label}): "
                   f"\"{_clip(t.get('quote') or '', 110)}\"")
        if len(out) >= limit:
            return out
    try:
        for r in takes_mod.public_receipts(storage, league, season, names):
            if r.get("subject_roster_id") not in rids:
                continue
            out.append(f"  receipt on {r.get('subject_name')}: {r.get('status')} — "
                       f"\"{_clip(r.get('quote') or '', 110)}\"")
            if len(out) >= limit:
                break
    except Exception:
        pass
    return out


def _clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(",;:") + " …"


# ------------------------------------------------------------ moves

def recent_moves(storage: Storage, league: League, rid: int,
                 weeks_played: int, *, limit: int = 3) -> list[str]:
    """What this team just did, with the engine's reading of why.

    The rationale is an inference and is labeled as one. It is a place to
    start asking, never a motive to state.
    """
    from leaguepage.transaction_analysis import analyze_transactions, describe_move

    out, shown = [], 0
    for row in analyze_transactions(storage, league, weeks_played):
        if rid not in row.get("rids", []):
            continue
        bits = [f"wk {row['week']}"]
        if row.get("faab"):
            bits.append(f"{round(row['faab_share'] * 100)}% of budget")
        out.append(f"  {describe_move(row)} ({', '.join(bits)})")
        if (row.get("rationale") or {}).get("text"):
            out.append(f"    reads as: {row['rationale']['text']}")
        if row.get("rank_shift"):
            out.append(f"    moved them {row['rank_shift']}")
        shown += 1
        if shown >= limit:
            break
    return out or ["  no transactions on record for this team yet"]


def possible_moves(storage: Storage, league: League, profile: dict, team: dict,
                   *, weeks_played: int) -> list[str]:
    """What is still open to them: the hole, and what they have to spend.

    Not a recommendation. The Desk does not tell him what a manager should
    do; it tells him what a manager could do, which is what makes "and they
    did not" worth writing.
    """
    from leaguepage.team_analytics import is_rated, label_for_rank

    rid = team["roster_id"]
    roster = _roster(storage, league, rid)
    out = []
    budget = ((storage.get_league(league.league_id) or {})
              .get("settings") or {}).get("waiver_budget")
    used = (roster.get("settings") or {}).get("waiver_budget_used")
    if budget is not None and used is not None:
        out.append(f"  FAAB left: {budget - used} of {budget}")
    else:
        out.append("  FAAB left: not in the synced league settings")
    n = profile.get("n") or len(profile.get("teams") or []) or 0
    worst = []
    for pos, ranks in (profile.get("ranks") or {}).items():
        r = ranks.get(rid)
        if r is not None and is_rated(profile, pos, rid):
            worst.append((r, pos))
    worst.sort(reverse=True)
    for r, pos in worst[:2]:
        out.append(f"  thinnest at {pos} ({label_for_rank(r, n)}) — the slot a "
                   f"claim would be for")
    if not weeks_played:
        out.append("  week 1: the waiver wire has not opened on anything yet")
    return out


# ------------------------------------------------------------ roast ammo

def self_inflicted(storage: Storage, league: League, team: dict,
                   *, weeks_played: int, limit: int = 2) -> list[str]:
    """Roast ammunition, and only the kind that is on the record.

    Something they chose, that went badly, that can be shown: a pick taken
    well ahead of its reference rank, a player they drafted who is on
    nobody's roster now, points left on their own bench. Never a guess
    about what they were thinking, and never a joke written for him.
    """
    from leaguepage.adp import load_adp_for_league
    from leaguepage.draft_aging import draft_aging
    from leaguepage.draft_analysis import analyze_league_draft
    from leaguepage.draft_value import (CLASS_REACH, SKILL_POSITIONS,
                                        classify_pick)

    rid = team["roster_id"]
    out = []
    eff = team.get("lineup_efficiency")
    if eff is not None and eff < 0.9:
        out.append(f"  left {round((1 - eff) * 100)}% of their own best lineup "
                   f"on the bench this week")

    # Without the reference board every delta is None and no pick is a
    # reach, so the whole section would quietly report nothing to say.
    analysis = analyze_league_draft(storage, league,
                                    adp=load_adp_for_league(league))
    named: set[str] = set()
    size = (analysis or {}).get("total_teams") or 0
    for t in (analysis or {}).get("teams", []):
        if t.get("roster_id") != rid:
            continue
        picks = sorted((p for p in (t.get("picks_by_round") or [])
                        if p.get("delta") is not None),
                       key=lambda p: p["delta"])
        for r in picks:
            # Kickers and defenses are the whole top of every reach list,
            # because expert consensus ranks special teams below the
            # draftable range while lineups force everyone to draft one.
            # That measures the board, not a decision he can roast.
            if r.get("position") not in SKILL_POSITIONS:
                continue
            cls = classify_pick(r["delta"], size, off_board=r.get("off_board", False))
            # The project's own bar for a reach: a full round early or more.
            # Below that it is a preference, and calling an eleven-pick
            # reach ammunition would send him to write about nothing.
            if not cls or cls["draft_value_class"] != CLASS_REACH:
                continue
            out.append(f"  took {r['name']} at pick {r['pick_no']}, "
                       f"{cls['label'].removeprefix('REACH · ')}")
            named.add(r["name"])
            if len(named) >= limit:
                break
        break

    try:
        rows = draft_aging(storage, league).get(rid) or []
    except Exception:
        rows = []
    shown = 0
    for r in rows:
        if r.get("status") != "gone" or r["name"] in named:
            continue
        out.append(f"  drafted {r['name']} at pick {r['pick_no']} and he is on "
                   f"nobody's roster now")
        named.add(r["name"])
        shown += 1
        if shown >= limit:
            break
    return out or ["  nothing on the record against them yet"]


# ------------------------------------------------------------ callbacks

def notable_callback(matchup: dict, resolved: dict) -> tuple[str | None, list[str]]:
    """The prior meeting, but only if it was worth remembering.

    A callback to a routine week-3 win is filler that costs a paragraph and
    buys nothing, and it was going in every preview because history existed
    rather than because it mattered. So the bar is a reader's bar: it was
    close enough to have hurt, lopsided enough to have been embarrassing,
    or the highest score either of them has put up against the other.

    Returns (reason, lines). A None reason means say nothing about history.
    """
    h2h = matchup.get("h2h") or {}
    meetings = h2h.get("meetings") or []
    if not meetings:
        return None, []
    scored = [m for m in meetings if _margin(m) is not None]
    if not scored:
        return None, []
    last = scored[-1]
    margin = _margin(last)
    reason = None
    if margin <= CLOSE_MARGIN:
        reason = f"decided by {margin:.1f}"
    elif margin >= BLOWOUT_MARGIN:
        reason = f"a {margin:.1f}-point beating"
    elif len(scored) > 1 and _high(last) > max(_high(x) for x in scored[:-1]):
        # Only against a field. With one prior meeting the last one is
        # trivially the highest, and this rule would wave every single
        # piece of history through -- which is the filter doing nothing.
        reason = "the highest score either has put on the other"
    if reason is None:
        return None, []
    winner = _name(resolved, last.get("winner")) if last.get("winner") else "nobody"
    return reason, [f"  week {last['week']}: {last['points']} — {reason}, "
                    f"won by {winner}"]


def _points(meeting: dict) -> list[float]:
    raw = meeting.get("points")
    if isinstance(raw, str):
        try:
            return [float(x.strip()) for x in raw.replace("–", "-").split("-")]
        except ValueError:
            return []
    if isinstance(raw, (list, tuple)):
        return [float(x) for x in raw if x is not None]
    return []


def _margin(meeting: dict) -> float | None:
    pts = _points(meeting)
    return abs(pts[0] - pts[1]) if len(pts) == 2 else None


def _high(meeting: dict) -> float:
    pts = _points(meeting)
    return max(pts) if pts else 0.0
