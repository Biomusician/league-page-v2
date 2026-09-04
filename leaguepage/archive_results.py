"""Recover a season's results from the records printed in its previews.

The archive states almost no results as results. Every `Matchup Roundup`
block is a PREVIEW, written before the games: two teams, their records
coming in, and a win probability. Nobody wrote down who won.

But a preview states each team's record, and the next week's preview states
it again. If a team was 3-1 in week 5's preview and 4-1 in week 6's, it won
in week 5. Comparing consecutive issues recovers the result without anyone
having recorded it.

This is inference, and it is labelled as inference everywhere it surfaces.
Two rules keep it honest:

**It fires only on an unambiguous pair.** One side must gain exactly one win
and the other exactly one loss. Anything else — both teams gaining a win, a
team gaining two, a team missing from the next issue — produces nothing. The
failure mode is a miss, never a wrong result.

**A week whose own headers disagree with themselves is not used at all.**
The corpus has real defects: a copy-paste error puts one team in two
matchups in the same week, a record is typed `(63-9)`, a win probability is
written `52-48` instead of `52/48`. Each is caught by a check that does not
depend on noticing that particular defect:

* every record in week N must sum to N-1 games, because that is what a
  record means in week N;
* no team may appear twice in one week;
* records only ever come from inside parentheses, so a probability written
  with a dash can never be read as one.

The one identity question the corpus answers itself. A roster drafted by a
proxy appears under two names across the season, and week 1 writes the
header as `The Dude/Glory` — the author naming both for one team. The parser
learns aliases from that compound form rather than being told them, so the
mapping is evidence from the corpus rather than an assumption about who
somebody is.
"""
from __future__ import annotations

import re
from collections import defaultdict

# `Optional Label: NAME (W-L) vs NAME (W-L)`. Records live inside
# parentheses and nowhere else, which is what keeps the trailing win
# probability -- sometimes written `52-48` -- from being read as one.
_HEADER = re.compile(
    r"^[^\n:]{0,40}?:?\s*"
    r"(?P<a>[A-Za-z][A-Za-z.'/ ]{1,28}?)\s*\((?P<ra>\d{1,2})\s*[-/]\s*(?P<la>\d{1,2})\)"
    r"\s*vs\.?\s*"
    r"(?P<b>[A-Za-z][A-Za-z.'/ ]{1,28}?)\s*\((?P<rb>\d{1,2})\s*[-/]\s*(?P<lb>\d{1,2})\)",
    re.M,
)


def canonical(name: str) -> str:
    """One spelling per franchise. `PITCH` and `Pitch` are one team."""
    return " ".join((name or "").split()).strip(" .").casefold()


def learn_aliases(texts: list[str]) -> dict[str, str]:
    """alias -> canonical, learned from compound `A/B` headers.

    A roster drafted on someone else's behalf shows up under both names
    across a season. The author wrote one header as `The Dude/Glory`, which
    is the corpus saying the two are one team. The first name wins because
    it is the one the franchise carries in every other week.
    """
    out: dict[str, str] = {}
    for text in texts:
        for m in _HEADER.finditer(text):
            for raw in (m.group("a"), m.group("b")):
                if "/" not in raw:
                    continue
                parts = [canonical(p) for p in raw.split("/") if canonical(p)]
                if len(parts) < 2:
                    continue
                for other in parts[1:]:
                    out[other] = parts[0]
                out[canonical(raw)] = parts[0]
    return out


def _resolve(name: str, aliases: dict[str, str]) -> str:
    c = canonical(name)
    return aliases.get(c, c)


def display_names(texts: list[str], aliases: dict[str, str]) -> dict[str, str]:
    """canonical -> the spelling the newsletters used most often.

    Case-folding is what lets `PITCH` and `Pitch` be one team; it is not
    what should be printed. The most frequent original spelling wins, so a
    single shouty week does not rename a franchise.
    """
    counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for text in texts:
        for m in _HEADER.finditer(text):
            for side in ("a", "b"):
                raw = " ".join(m.group(side).split()).strip(" .")
                if "/" in raw:
                    raw = raw.split("/")[0].strip()
                counts[_resolve(raw, aliases)][raw] += 1
    return {c: max(v.items(), key=lambda kv: (kv[1], kv[0]))[0]
            for c, v in counts.items()}


def week_pairings(text: str, week: int, aliases: dict[str, str]) -> dict:
    """One issue's matchup headers, with the week's own integrity checked.

    Returns {'pairs', 'records', 'usable', 'why'}. `usable` is False when the
    headers contradict each other, and then the week contributes nothing:
    there is no way to tell which of two conflicting lines is the mistake.
    """
    pairs: list[tuple[str, str]] = []
    records: dict[str, tuple[int, int]] = {}
    seen: list[str] = []
    dropped: list[str] = []
    for m in _HEADER.finditer(text):
        row = []
        for side, w_key, l_key in (("a", "ra", "la"), ("b", "rb", "lb")):
            name = _resolve(m.group(side), aliases)
            wins, losses = int(m.group(w_key)), int(m.group(l_key))
            # A record in week N covers the N-1 games already played. A row
            # that fails this is a typo, and there is no way to know which
            # half of it is wrong.
            if wins + losses != week - 1:
                dropped.append(f"{name} ({wins}-{losses}) in week {week}")
                row = []
                break
            row.append((name, wins, losses))
        if len(row) != 2:
            continue
        for name, wins, losses in row:
            seen.append(name)
            records[name] = (wins, losses)
        pairs.append((row[0][0], row[1][0]))

    repeated = sorted({n for n in seen if seen.count(n) > 1})
    why = []
    if repeated:
        why.append("a team appears in two matchups: "
                   + ", ".join(repeated))
    if dropped:
        why.append("record does not match the week: " + "; ".join(dropped))
    return {"pairs": pairs, "records": records,
            "usable": not repeated and bool(pairs), "why": why}


def reconstruct(issues: list[dict]) -> dict:
    """Results inferred from consecutive issues of one league season.

    `issues`: [{'week', 'body', 'issue_id', 'title'}], any order. Returns
    {'results', 'standings', 'weeks_covered', 'skipped', 'unresolved'}.
    """
    by_week = {int(i["week"]): i for i in issues
               if i.get("week") and i.get("body")}
    aliases = learn_aliases([i["body"] for i in by_week.values()])
    parsed = {wk: week_pairings(i["body"], wk, aliases)
              for wk, i in sorted(by_week.items())}

    skipped = [{"week": wk, "why": "; ".join(p["why"]) or "no matchup headers"}
               for wk, p in sorted(parsed.items()) if not p["usable"]]

    results: list[dict] = []
    unresolved: list[dict] = []
    for wk in sorted(parsed):
        here, nxt = parsed.get(wk), parsed.get(wk + 1)
        if not here or not here["usable"] or not nxt or not nxt["usable"]:
            continue
        for a, b in here["pairs"]:
            before_a, before_b = here["records"].get(a), here["records"].get(b)
            after_a, after_b = nxt["records"].get(a), nxt["records"].get(b)
            if not all((before_a, before_b, after_a, after_b)):
                unresolved.append({"week": wk, "teams": [a, b],
                                   "why": "a team is not in the next issue"})
                continue
            da = (after_a[0] - before_a[0], after_a[1] - before_a[1])
            db = (after_b[0] - before_b[0], after_b[1] - before_b[1])
            if da == (1, 0) and db == (0, 1):
                winner, loser = a, b
            elif da == (0, 1) and db == (1, 0):
                winner, loser = b, a
            else:
                unresolved.append({"week": wk, "teams": [a, b],
                                   "why": f"records moved {da} and {db}"})
                continue
            results.append({
                "week": wk, "winner": winner, "loser": loser,
                "winner_record_after": nxt["records"][winner],
                "from_issues": [by_week[wk]["issue_id"],
                                by_week[wk + 1]["issue_id"]],
            })

    standings: dict[str, dict] = defaultdict(lambda: {"wins": 0, "losses": 0})
    for r in results:
        standings[r["winner"]]["wins"] += 1
        standings[r["loser"]]["losses"] += 1

    weeks = sorted({r["week"] for r in results})
    names = display_names([i["body"] for i in by_week.values()], aliases)
    for r in results:
        r["winner_name"] = names.get(r["winner"], r["winner"])
        r["loser_name"] = names.get(r["loser"], r["loser"])
    return {
        "results": results,
        "standings": dict(standings),
        "names": names,
        "weeks_covered": weeks,
        "skipped": skipped,
        "unresolved": unresolved,
    }


def head_to_head(results: list[dict]) -> dict[tuple[str, str], dict]:
    """(team, opponent) -> {'wins', 'losses'} over the reconstructed games."""
    out: dict[tuple[str, str], dict] = defaultdict(
        lambda: {"wins": 0, "losses": 0})
    for r in results:
        out[(r["winner"], r["loser"])]["wins"] += 1
        out[(r["loser"], r["winner"])]["losses"] += 1
    return dict(out)


# ------------------------------------------------------- the site surface

# A season is only worth publishing when enough of it came back. Three
# weeks of scattered results is a curiosity; most of a season is a record.
MIN_WEEKS = 6


def season_results(storage, league) -> list[dict]:
    """Every season of this league's archive that reconstructs cleanly.

    Scoped exactly as archive callbacks are: a league only ever sees its own
    corpus, so a defunct third league's results can never surface under a
    masthead they were never part of.
    """
    from leaguepage.story_memory import ARCHIVE_SCOPE

    by_season: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for slug_key in (ARCHIVE_SCOPE.get(league.slug) or []):
        for item in storage.list_archive_issues(slug_key):
            if not item.get("season") or not item.get("week"):
                continue
            full = storage.get_archive_issue(item["issue_id"]) or {}
            by_season[(slug_key, item["season"])].append({
                "week": item["week"], "issue_id": item["issue_id"],
                "title": item.get("title") or "archive issue",
                "body": full.get("body") or "",
            })

    out = []
    for (_slug, season), issues in sorted(by_season.items(), reverse=True):
        rec = reconstruct(issues)
        if len(rec["weeks_covered"]) < MIN_WEEKS:
            continue
        titles = {i["issue_id"]: i["title"] for i in issues}
        rec["season"] = season
        rec["issue_titles"] = titles
        out.append(rec)
    return out


def standings_rows(rec: dict) -> list[dict]:
    """Reconstructed standings, best record first."""
    rows = []
    for key, r in rec["standings"].items():
        games = r["wins"] + r["losses"]
        rows.append({
            "key": key,
            "name": rec["names"].get(key, key),
            "wins": r["wins"], "losses": r["losses"], "games": games,
            "pct": round(r["wins"] / games, 3) if games else 0.0,
        })
    rows.sort(key=lambda r: (-r["pct"], -r["wins"], r["name"].lower()))
    return rows


def weeks_rows(rec: dict) -> list[dict]:
    """Results grouped by week, in order."""
    by_week: dict[int, list[dict]] = defaultdict(list)
    for r in rec["results"]:
        by_week[r["week"]].append(r)
    return [{"week": wk, "games": sorted(by_week[wk],
                                         key=lambda g: g["winner_name"].lower())}
            for wk in sorted(by_week)]


def coverage_note(rec: dict) -> str:
    """What was recovered, what was not, and why — in the reader's words."""
    weeks = rec["weeks_covered"]
    n = len(rec["results"])
    span = (f"weeks {weeks[0]}\u2013{weeks[-1]}" if weeks else "no weeks")
    missing = sorted({w for w in range(min(weeks), max(weeks) + 1)
                      if w not in weeks}) if weeks else []
    parts = [f"{n} games recovered across {span}."]
    if missing:
        parts.append(
            "Weeks " + ", ".join(str(w) for w in missing) + " are missing "
            "because the issue on either side of them contradicts itself, "
            "and a week that cannot be trusted is left out rather than "
            "guessed at.")
    return " ".join(parts)


def resolve_result_teams(rec: dict, teams: list[dict], managers: dict) -> dict:
    """canonical name -> current team slug, via CONFIRMED aliases only.

    An unconfirmed alias is a guess, and a guess printed as "this is who
    won" is the claim this system exists not to make. A name that does not
    resolve still prints; it just does not become a link.
    """
    from leaguepage.editorial import confirmed_aliases

    by_alias: dict[str, dict] = {}
    for t in teams:
        for key in t.get("manager_keys", []) or []:
            for alias in confirmed_aliases(managers.get(key) or {}):
                by_alias.setdefault(canonical(alias), t)
    return {k: (by_alias.get(k) or {}).get("team_slug")
            for k in rec["names"]}


def drop_private_results(recs: list[dict], private_handles: list[str]) -> list[dict]:
    """Seasons every one of whose names the site may print.

    A manager who leaves the league keeps his 2021 results and loses the
    public team name that made his handle publishable. Publishing half a
    season would misstate every record in it, so the season goes rather
    than the row.
    """
    if not private_handles:
        return recs
    from leaguepage.privacy import handle_re

    pats = [handle_re(h) for h in private_handles]
    return [rec for rec in recs
            if not any(p.search(n) for n in rec["names"].values() for p in pats)]


def title_tension(rows: list[dict], season: str, ledger: list[dict]) -> str | None:
    """One line where the reconstruction and the title ledger disagree.

    They are two independent readings of the same archive: the ledger is
    what the masthead recorded, and these standings are worked out from the
    weekly records. When the team that led the recovered weeks is not the
    team that won, that is the season's story and the page should say it
    rather than leave a reader to notice two tables contradicting.

    Returns None when they agree, when the ledger has nothing for this
    season, or when the lead is shared -- a co-leader is not an upset.
    """
    if not rows:
        return None
    champ = next((r["champion"] for r in ledger
                  if str(r.get("season")) == str(season)), None)
    if not champ:
        return None
    top = [r for r in rows if r["pct"] == rows[0]["pct"]]
    if len(top) != 1:
        return None
    leader = top[0]
    if canonical(leader["name"]) == canonical(champ):
        return (f"{leader['name']} led the recovered weeks at "
                f"{leader['wins']}-{leader['losses']} and won the title.")
    return (f"{leader['name']} led the recovered weeks at "
            f"{leader['wins']}-{leader['losses']}. The masthead records "
            f"{champ} as the {season} champion, so the season did not end "
            f"the way the regular season ran.")
