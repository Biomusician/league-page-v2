"""The front page: what is new, what matters, what to read next.

The old league home was a newsletter excerpt, then a standings table, then
the archive — the same three modules in the same order in August as in
December. In August that table read 0-0 / 0.0 PF twelve times, which is a
row of zeroes where the most interesting week of the year should be.

So the front page is season-state-aware. `season_state()` decides which
signals are worth a reader's first fifteen seconds, item builders each
propose stories carrying a weight, and the strongest becomes the lead while
the next few become secondary. Nothing is padded: if only three items are
real, three items ship.

Two rules the copy obeys everywhere in this module:

* **It never imitates the Commissioner.** No jokes, no analogies, no
  verdicts on people. It reads like an intelligent briefing, which is
  exactly what makes his voice worth more when it appears two inches below.
* **Every item goes somewhere.** An item without an `href` is not a story,
  it is a fact, and facts belong on the data pages.
"""
from __future__ import annotations

from leaguepage.draft_value import SKILL_POSITIONS

PRESEASON = "preseason"
OPENING = "opening"
MIDSEASON = "midseason"
PLAYOFF_RACE = "playoff_race"
POSTSEASON = "postseason"

STATE_LABELS = {
    PRESEASON: "Preseason",
    OPENING: "Opening weeks",
    MIDSEASON: "Midseason",
    PLAYOFF_RACE: "Playoff race",
    POSTSEASON: "Postseason",
}

# How many stories the front page will carry. Below the floor the briefing
# is suppressed entirely rather than shown half-empty.
MAX_ITEMS = 5
MIN_ITEMS = 2
OPENING_WEEKS = 3          # before this the table means very little
PLAYOFF_RACE_WINDOW = 4    # weeks before the playoffs when leverage leads


def season_state(weeks_played: int, week: int, playoff_week_start: int | None) -> str:
    """Which half of the year the reader is in.

    Driven by games actually played, never by the calendar: a league whose
    week counter has advanced but whose matchups are all zeroes is still
    preseason as far as a reader is concerned."""
    if weeks_played <= 0:
        return PRESEASON
    start = int(playoff_week_start or 15)
    if week >= start:
        return POSTSEASON
    if weeks_played < OPENING_WEEKS:
        return OPENING
    if week >= start - PLAYOFF_RACE_WINDOW:
        return PLAYOFF_RACE
    return MIDSEASON


def freshness_line(state: str, weeks_played: int, week: int,
                   last_sync: str | None) -> str:
    """One line telling a visitor whether the site knows what they know."""
    synced = f" · synced {last_sync[:10]}" if last_sync else ""
    if state == PRESEASON:
        return f"Preseason · no games played yet{synced}"
    if state == POSTSEASON:
        return f"Postseason · through week {weeks_played}{synced}"
    return f"Updated after Week {weeks_played}{synced}"


# --------------------------------------------------------------- items


def _item(kind, label, headline, detail, href, *, weight, tags=(), cta=None):
    return {"kind": kind, "label": label, "headline": headline, "detail": detail,
            "href": href, "weight": weight, "tags": list(tags),
            "cta": cta or "More"}


def _game_of_the_week(ctx) -> list[dict]:
    cards = ctx.get("cards") or []
    if not cards:
        return []
    top = cards[0]
    state = ctx["state"]
    if state == PRESEASON:
        detail = "Week 1 opener — the first read on both rosters."
    elif top.get("score"):
        detail = f"Final: {top['score']}."
    else:
        detail = f"{top['records']} going in."
    return [_item(
        "game", "Game of the Week", top["names"], detail,
        f"matchups/index.html#{top['anchor']}",
        weight=88 if state != PRESEASON else 84,
        tags=top.get("tags") or [],
        cta="Common Tactical Picture")]


def _biggest_move(ctx) -> list[dict]:
    moves = [m for m in (ctx.get("moves") or [])]
    if not moves:
        return []
    top = max(moves, key=lambda m: (m.get("questionable", False), m.get("priority", 0)))
    tags = ["QUESTIONABLE MOVE"] if top.get("questionable") else []
    detail = top.get("text") or ""
    return [_item(
        "move", "Biggest Move", f"{top['team']}: {top['line']}",
        detail, "transactions/index.html",
        weight=86 if top.get("questionable") else 70,
        tags=tags, cta="Force Flow")]


def _team_to_watch(ctx) -> list[dict]:
    """Preseason: the sharpest construction contrast in the league. Later:
    whoever is actually scoring."""
    profile, names = ctx.get("profile"), ctx["names"]
    # The author does not headline his own newsletter. Team to Watch is a
    # purely promotional slot with no news value behind it, so his team is
    # simply not eligible for it — same rule as FEATURE prominence on
    # matchups, applied to the front page.
    author = ctx.get("author_roster_id")
    if ctx["state"] in (PRESEASON, OPENING) and profile:
        best = None
        for rid in profile["teams"]:
            if rid == author:
                continue
            ranks = {p: profile["ranks"][p][rid] for p in profile["positions"]
                     if p in SKILL_POSITIONS}
            if len(ranks) < 2:
                continue
            spread = max(ranks.values()) - min(ranks.values())
            if best is None or spread > best[0]:
                strong = min(ranks, key=ranks.get)
                weak = max(ranks, key=ranks.get)
                best = (spread, rid, strong, ranks[strong], weak, ranks[weak])
        if best and best[0] >= max(3, round(0.5 * profile["n"])):
            _, rid, strong, s_rank, weak, w_rank = best
            return [_item(
                "watch", "Team to Watch", names.get(rid, f"Roster {rid}"),
                f"The widest gap on the board: {strong} #{s_rank} of "
                f"{profile['n']}, {weak} #{w_rank}. One room carries it and "
                "one room can sink it.",
                f"team/{ctx['slugs'][rid]}/index.html",
                weight=72, cta="Team page")]
        return []
    form = {rid: f for rid, f in (ctx.get("form") or {}).items() if rid != author}
    if not form:
        return []
    best_rid = min(form, key=lambda rid: form[rid]["rank"])
    f = form[best_rid]
    if f["rank"] > 1:
        return []
    return [_item(
        "watch", "Team to Watch", ctx["names"].get(best_rid, f"Roster {best_rid}"),
        f"#1 scoring over the last {f['window']}.",
        f"team/{ctx['slugs'][best_rid]}/index.html",
        weight=74, cta="Team page")]


def _consensus_defiance(ctx) -> list[dict]:
    """Preseason and opening weeks only: the boldest call anyone made. K and
    DST are excluded upstream, so this is always a roster decision."""
    if ctx["state"] not in (PRESEASON, OPENING):
        return []
    reaches = ctx.get("reaches") or []
    steals = ctx.get("steals") or []
    out = []
    if reaches:
        p = reaches[0]
        out.append(_item(
            "draft", "Boldest Call", f"{p['team']} took {p['name']}",
            f"{p['dv']['label']} against the consensus board. The single "
            "largest departure from the market in this draft.",
            "draft/index.html", weight=76, tags=["REACH"], cta="Draft board"))
    if steals:
        p = steals[0]
        out.append(_item(
            "draft", "Best Value", f"{p['team']} got {p['name']}",
            f"{p['dv']['label']} — the biggest thing the room let fall.",
            "draft/index.html", weight=64, tags=["STEAL"], cta="Draft board"))
    return out


def _standings_movement(ctx) -> list[dict]:
    movers = ctx.get("movers") or []
    if ctx["state"] == PRESEASON or not movers:
        return []
    return [_item(
        "standings", "Biggest Riser", movers[0],
        "Standings movement since the last snapshot.",
        "standings/index.html", weight=80, cta="Standings")]


def _form_stories(ctx) -> list[dict]:
    out = []
    for line in (ctx.get("hot") or [])[:1]:
        out.append(_item("hot", "Running Hot", line,
                         "Scoring, not record — the two disagree more often "
                         "than anyone admits.",
                         "standings/index.html", weight=68, cta="Standings"))
    for line in (ctx.get("trouble") or [])[:1]:
        out.append(_item("trouble", "In Trouble", line,
                         "Bottom of the league on points over the recent window.",
                         "standings/index.html", weight=66, cta="Standings"))
    return out


def _playoff_leverage(ctx) -> list[dict]:
    if ctx["state"] not in (PLAYOFF_RACE, POSTSEASON):
        return []
    playoff = ctx.get("playoff") or {}
    rows = playoff.get("rows") or []
    if not rows or not playoff.get("spots"):
        return []
    cut = int(playoff["spots"])
    if len(rows) <= cut:
        return []
    inside, outside = rows[cut - 1], rows[cut]
    return [_item(
        "playoff", "The Cutline", f"{inside['name']} vs {outside['name']}",
        f"Seed {cut} and the first team outside it"
        + (f": {inside['odds']} against {outside['odds']}." if inside.get("odds")
           else f": {inside['band']} against {outside['band']}."),
        "standings/index.html", weight=94, cta="Playoff picture")]


def _receipt(ctx) -> list[dict]:
    """A prior published claim that current data is now testing. Supplied by
    the receipts layer; the front page only ranks and renders it."""
    r = ctx.get("receipt")
    if not r:
        return []
    return [_item(
        "receipt", "Receipt", r["claim"], r["status_note"], r["href"],
        weight=r.get("weight", 78), tags=[r["status"]], cta=r.get("cta", "The issue"))]


BUILDERS = (_playoff_leverage, _game_of_the_week, _biggest_move,
            _standings_movement, _receipt, _consensus_defiance,
            _team_to_watch, _form_stories)


# --------------------------------------------------------------- assembly


def strongest_rooms(profile: dict, names: dict[int, str],
                    slugs: dict[int, str]) -> list[dict]:
    """Preseason replacement for a table of zeroes: who owns each skill
    position. K and DST are deliberately absent — a kicker room has never
    been the reason to click into a team page in August."""
    if not profile:
        return []
    rows = []
    for pos in profile["positions"]:
        if pos not in SKILL_POSITIONS:
            continue
        ranked = profile["ranks"][pos]
        rid = min(ranked, key=ranked.get)
        rows.append({"pos": pos, "name": names.get(rid, f"Roster {rid}"),
                     "slug": slugs.get(rid), "n": profile["n"]})
    return rows


def build(ctx: dict) -> dict:
    """The whole front-page model.

    `ctx` is the builder's already-computed material — no recomputation
    here, and no storage access, which keeps this testable with plain
    dictionaries."""
    weeks_played = int(ctx.get("weeks_played") or 0)
    week = int(ctx.get("week") or 1)
    state = season_state(weeks_played, week, ctx.get("playoff_week_start"))
    ctx = {**ctx, "state": state}

    items: list[dict] = []
    seen_kinds: set[str] = set()
    for builder in BUILDERS:
        for item in builder(ctx):
            if item["kind"] in seen_kinds:
                continue
            seen_kinds.add(item["kind"])
            items.append(item)
    items.sort(key=lambda i: -i["weight"])
    items = items[:MAX_ITEMS]

    show_standings = weeks_played > 0
    return {
        "state": state,
        "state_label": STATE_LABELS[state],
        "freshness": freshness_line(state, weeks_played, week, ctx.get("last_sync")),
        "briefing": items if len(items) >= MIN_ITEMS else [],
        "lead": items[0] if items else None,
        "secondary": items[1:] if len(items) >= MIN_ITEMS else [],
        "show_standings": show_standings,
        "standings_note": (None if show_standings else
                           "Standings open once games are played; before then the "
                           "table is a row of zeroes and a random tiebreak."),
        "rooms": (strongest_rooms(ctx.get("profile"), ctx["names"], ctx["slugs"])
                  if not show_standings else []),
        "weeks_played": weeks_played,
    }
