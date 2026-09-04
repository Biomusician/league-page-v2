"""What the Commissioner should do next, and what is stopping him.

The Desk home used to open on a SYNC button and two lines of status per
league: an issue status word, a draft status word, a pick count. All true,
none of it an answer to the question a person actually arrives with, which
is "where was I, and what is in my way this week".

So this computes the answer instead of the inputs. Per league:

* how stale the data is, in the units a person thinks in;
* how many things changed that he has not looked at, and how many of those
  are worth looking at;
* where the week's issue stands, section by section, as counts rather than
  a single word that hides eleven modules behind it;
* what would refuse to publish right now;
* and one next action, as a link.

The rule for the next action is that it names the earliest step that is
actually blocked. Telling somebody to publish while four sections are empty
is not guidance, it is a button.

Nothing here decides anything. It does not approve, include, exclude, or
publish; every count is a read, and every action is a link to the screen
where the Commissioner makes the call himself.
"""
from __future__ import annotations

from datetime import datetime, timezone

from leaguepage.config import League
from leaguepage.storage import Storage

# Older than this and the board is describing a league that has moved on.
SYNC_STALE_HOURS = 20
# An inbox item at or above this significance is worth a look now.
WORTH_A_LOOK = 60


def _age(stamp: str | None) -> tuple[float | None, str]:
    """Hours since an ISO timestamp, and how a person would say it."""
    if not stamp:
        return None, "never"
    try:
        when = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None, "unknown"
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    hours = (datetime.now(timezone.utc) - when).total_seconds() / 3600
    if hours < 1:
        return hours, "just now"
    if hours < 24:
        return hours, f"{int(hours)}h ago"
    days = int(hours // 24)
    return hours, f"{days} day{'' if days == 1 else 's'} ago"


def _section_counts(storage: Storage, league: League, season: str,
                    issue_key: str, week: int | None) -> dict:
    """Included modules by lifecycle state. A single status word on the issue
    hides eleven modules behind it, which is exactly the thing that makes a
    Sunday evening feel longer than it is."""
    from leaguepage.issue_builder import module_states

    try:
        rows = [m for m in module_states(storage, league, season, issue_key,
                                         week=week) if m.get("included")]
    except Exception:                                   # noqa: BLE001
        # A missing editorial directory is a normal preseason state, not an
        # error worth taking the whole Desk home down for.
        return {"total": 0, "approved": 0, "drafted": 0, "empty": 0, "rows": []}
    approved = sum(1 for m in rows if m.get("approved"))
    empty = sum(1 for m in rows
                if not m.get("approved") and m.get("status") in ("empty", "missing"))
    return {"total": len(rows), "approved": approved,
            "drafted": len(rows) - approved - empty, "empty": empty,
            "rows": rows}


def _blockers(storage: Storage, league: League, season: str,
              issue_key: str) -> list[str]:
    """What would refuse to publish, asked before he walks into it."""
    from leaguepage.issue_builder import assemble_issue

    try:
        result = assemble_issue(storage, league, season, issue_key,
                                enforce=False)
    except Exception as exc:                            # noqa: BLE001
        return [f"assembly failed: {type(exc).__name__}"]
    return list(result.get("warnings") or [])


def league_status(storage: Storage, league: League) -> dict:
    """One league's answer to "where am I".

    Every number is a read. Nothing here approves, includes, excludes or
    publishes anything.
    """
    from leaguepage import change_inbox as ci
    from leaguepage import sync_jobs

    data = storage.get_league(league.league_id) or {}
    season = str(data.get("season") or "")
    week = int(storage.get_meta("current_week") or 1)
    issue_key = f"week-{week:02d}"

    hours, said = _age(storage.get_meta(sync_jobs.LAST_SYNC_KEY))
    out = {
        "league": league, "season": season, "week": week,
        "issue_key": issue_key,
        "sync_age": said,
        "sync_stale": hours is None or hours >= SYNC_STALE_HOURS,
        "undecided": 0, "worth_a_look": 0,
        "sections": {"total": 0, "approved": 0, "drafted": 0, "empty": 0, "rows": []},
        "blockers": [],
        "next_action": None,
    }
    if not season:
        out["next_action"] = {"text": "Sync Sleeper to pick up this league",
                              "href": "/commissioner#syncpanel",
                              "why": "no season on file"}
        return out

    board = ci.build_inbox(storage, league, season)
    undecided = [i for i in (board.get("items") or []) if not i.get("decision")]
    out["undecided"] = len(undecided)
    out["worth_a_look"] = sum(1 for i in undecided
                              if (i.get("score") or 0) >= WORTH_A_LOOK)

    out["sections"] = _section_counts(storage, league, season, issue_key, week)
    issue = storage.get_issue(league.slug, season, issue_key)
    out["issue_status"] = (issue or {}).get("status") or "not started"
    if out["issue_status"] not in ("published",):
        out["blockers"] = _blockers(storage, league, season, issue_key)

    out["next_action"] = _next_action(out)
    return out


def _next_action(row: dict) -> dict:
    """The earliest step that is actually blocked.

    Ordering matters more than the wording. Suggesting "publish" while four
    sections are empty is not guidance; it is a button that will refuse.
    """
    lg, season, week = row["league"], row["season"], row["week"]
    base = f"/commissioner/{lg.slug}/{season}"
    key = row["issue_key"]
    sec = row["sections"]

    if row["sync_stale"]:
        return {"text": "Sync Sleeper", "href": "/commissioner#syncpanel",
                "why": f"last sync {row['sync_age']}"}
    if row["worth_a_look"]:
        n = row["worth_a_look"]
        return {"text": f"Triage {n} item{'' if n == 1 else 's'} in the Change Inbox",
                "href": "/commissioner/inbox",
                "why": f"{row['undecided']} undecided, {n} above the noise floor"}
    if sec["empty"]:
        n = sec["empty"]
        return {"text": f"Write {n} empty section{'' if n == 1 else 's'}",
                "href": f"{base}/issue/{key}/edit",
                "why": f"{sec['approved']} of {sec['total']} approved"}
    if sec["drafted"]:
        n = sec["drafted"]
        return {"text": f"Review and approve {n} section{'' if n == 1 else 's'}",
                "href": f"{base}/issue/{key}/review",
                "why": "written but not approved"}
    if row["blockers"]:
        return {"text": f"Clear {len(row['blockers'])} publication blocker(s)",
                "href": f"{base}/issue/{key}/edit",
                "why": row["blockers"][0][:120]}
    if sec["total"] and sec["approved"] == sec["total"]:
        return {"text": f"Preview and publish week {week}",
                "href": f"{base}/issue/{key}/publish",
                "why": "every included section is approved"}
    if row["undecided"]:
        return {"text": f"Clear {row['undecided']} inbox item(s)",
                "href": "/commissioner/inbox", "why": "nothing urgent, but not empty"}
    return {"text": f"Open week {week}", "href": f"{base}/issue/{key}/edit",
            "why": "nothing is waiting on you"}


def mission_control(storage: Storage, leagues: list[League]) -> list[dict]:
    return [league_status(storage, lg) for lg in leagues]
