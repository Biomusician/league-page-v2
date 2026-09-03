"""Six seasons of titles that Sleeper cannot tell you about.

The Sleeper API reaches back one previous season. The `Seasons Past` block
that ran in the masthead of thirty-three Disco issues reaches back to 2019,
including 2024 — a season for which no issue exists at all, because the 2025
issues carried the ledger forward.

This is the one thing in the archive worth parsing by machine. Everything
else in those newsletters names its winner in free prose, in every syntactic
position an English sentence allows, including hedges ("though it could go to
X for failing to...") and issues where the award went to nobody. A parser
over that tops out around 70% precision and its failure mode is telling the
league that somebody won Worst Decision when the text explicitly let them
off. That is not worth shipping. This is:

    2019
    Winner: <handle> / Loser: <handle>

Ninety-five matches across the corpus, zero false positives on a full hand
pass, and the ledger validates itself — the repeat markers "(2)" and "(x2)"
agree with the earlier rows they are counting.

Scope: the ledger belongs to the league whose masthead ran it. Big Daddy AF
is a defunct third league on a different scoring scale, and its records are
not this league's records; `story_memory.ARCHIVE_SCOPE` already refuses to
cross that line and nothing here relaxes it.
"""
from __future__ import annotations

import re

from leaguepage.config import League
from leaguepage.editorial import confirmed_aliases
from leaguepage.privacy import handle_re
from leaguepage.storage import Storage
from leaguepage.story_memory import ARCHIVE_SCOPE

# "Winner: X / Loser: Y" under a bare four-digit year. Anchored to the year
# line on purpose: there are thirty-one other `Winner:` lines in the corpus,
# every one a draft-review row ("Round 4: Winner: George Kittle (DIP x2)"),
# and a looser pattern reads those as championships.
_LEDGER_RE = re.compile(
    r"^[ \t]*(20\d\d)[ \t]*\r?\n[ \t]*Winner:[ \t]*(.+?)[ \t]*/[ \t]*Loser:[ \t]*(.+?)[ \t]*$",
    re.M)

# "(2)" and "(x2)" are the author counting repeats. They are evidence the
# ledger is self-consistent, and noise in a name.
_REPEAT_RE = re.compile(r"\s*\(x?\d+\)\s*$")


def _clean(name: str) -> str:
    return _REPEAT_RE.sub("", name).strip()


def _rows(body: str) -> list[tuple[str, str, str]]:
    out = []
    for season, winner, loser in _LEDGER_RE.findall(body or ""):
        w, l = _clean(winner), _clean(loser)
        # An in-season snapshot carries the current year with TBD in it.
        if not w or not l or "TBD" in (w.upper(), l.upper()):
            continue
        out.append((season, w, l))
    return out


def season_ledger(storage: Storage, league: League) -> list[dict]:
    """Champion and last place per season, newest first.

    The same season appears in every issue that ran the block, so the ledger
    is read from the LATEST issue that asserts it: a correction printed in a
    later issue is the author's own last word.
    """
    scope = ARCHIVE_SCOPE.get(league.slug) or []
    latest: dict[str, tuple[tuple, str, str]] = {}
    sources: dict[str, dict] = {}
    for slug_key in scope:
        for item in storage.list_archive_issues(slug_key):
            full = storage.get_archive_issue(item["issue_id"]) or {}
            # Sort key, not the season being reported: which issue said it.
            said_at = (item.get("season") or "", item.get("week") or 0,
                       item["issue_id"])
            for season, winner, loser in _rows(full.get("body")):
                if season in latest and latest[season][0] >= said_at:
                    continue
                latest[season] = (said_at, winner, loser)
                sources[season] = item
    out = []
    for season in sorted(latest, reverse=True):
        _at, winner, loser = latest[season]
        src = sources[season]
        out.append({
            "season": season,
            "champion": winner,
            "last_place": loser,
            "issue_id": src["issue_id"],
            "issue_title": src.get("title") or "archive issue",
            "href": f"archive/a{src['issue_id']}/index.html",
        })
    return out


def resolve_handles(rows: list[dict], teams: list[dict],
                    managers: dict[str, dict]) -> list[dict]:
    """Attach a current roster to each name, using CONFIRMED aliases only.

    An unconfirmed alias is a guess, and a guess printed as "this is who won
    in 2021" is the kind of claim this system exists not to make. A name that
    does not resolve still prints — it is what the newsletter said — it just
    does not become a link.

    The corpus contradicts itself about ownership in a handful of places (one
    franchise is credited to two different managers inside the same season;
    another roster was drafted by a proxy who then appears as a team name).
    Those all sit in the weekly award blocks, not in this ledger: every one
    of the ledger's names maps one-to-one, which is why it is the only thing
    here that ships.
    """
    by_alias: dict[str, dict] = {}
    for t in teams:
        for key in t.get("manager_keys", []) or []:
            for alias in confirmed_aliases(managers.get(key) or {}):
                by_alias.setdefault(alias.strip().lower(), t)
    out = []
    for row in rows:
        r = dict(row)
        for field in ("champion", "last_place"):
            team = by_alias.get(r[field].strip().lower())
            r[f"{field}_slug"] = (team or {}).get("team_slug")
            r[f"{field}_rid"] = (team or {}).get("roster_id")
        out.append(r)
    return out


def drop_private_names(rows: list[dict], private_handles: list[str]) -> list[dict]:
    """Rows whose names the site is allowed to print.

    Every name in today's ledger is one the manager published himself, inside
    his own team name. That is not guaranteed forever: a manager who leaves
    the league keeps his 2021 title and loses the public team name that made
    his handle publishable. Without this, that season would fail the build
    audit and take the whole deploy with it. Dropping the row instead is the
    same choice the archive callbacks make.
    """
    if not private_handles:
        return rows
    pats = [handle_re(h) for h in private_handles]
    return [r for r in rows
            if not any(p.search(r["champion"]) or p.search(r["last_place"])
                       for p in pats)]


def ledger_note(rows: list[dict]) -> str:
    """What this table is and is not, in one sentence."""
    if not rows:
        return ""
    span = f"{rows[-1]['season']}–{rows[0]['season']}"
    return (f"Read out of the masthead of the newsletters themselves, "
            f"{span}. Sleeper's records do not reach back this far; these do "
            f"because somebody wrote them down at the time.")
