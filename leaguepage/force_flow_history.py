"""What the Force Flow tab shows of the league's editorial past.

Force Flow is a standing page built from synced transactions. It used to be
a weekly newsletter section as well, and issues that carried one still do:
published snapshots are immutable, so the prose stays in the file. Two
rules follow from that.

**It is not archived as a section.** An archived issue no longer displays
its Force Flow section, because the reader has a live tab for that and a
newspaper does not reprint a standing feature inside every back issue. The
snapshot is not touched; the renderer simply skips the module.

**The tab may consume that history.** "Moves That Mattered" is the set of
transactions the Commissioner selected for a published issue. Those
selections are structured facts -- a story decision keyed by the move's own
candidate id -- so the tab reads them back against the synced log and gets
the team, the move and the cost as data rather than as a sentence to be
parsed. Nothing here recognises a team from prose. Where an old issue has
Force Flow prose but no structured selection behind it, the prose is shown
as it was published, labelled with its issue, and that is the whole extent
of the fallback.
"""
from __future__ import annotations

from leaguepage.config import League
from leaguepage.storage import Storage
from leaguepage.transaction_analysis import describe_move, story_candidate_id

# Modules that became standing league tabs. An issue that carried one still
# holds the prose in its snapshot; the public renderer omits it.
PERSISTENT_TAB_MODULES = {"forceflow"}


def move_unit(row: dict, names: dict[int, str]) -> dict:
    """One transaction as the public site presents it, team first.

    `teams` is structural -- roster ids resolved to canonical public names
    -- so a template can link each one through the page's own team map.
    The internal confidence field never leaves the analysis layer.
    """
    rids = list(row.get("rids") or [])
    teams = [{"rid": rid, "name": names.get(rid) or f"Roster {rid}"}
             for rid in rids]
    is_trade = row.get("type") == "trade"
    return {
        "txn_id": row.get("txn_id"),
        "teams": teams,
        # The same identity as one label, for the surfaces that print a
        # move in a sentence (the front page's Biggest Move). A trade
        # names both sides.
        "team": (" ↔ " if is_trade else ", ").join(t["name"] for t in teams),
        "is_trade": is_trade,
        "week": row["week"], "type": row["type"],
        "line": describe_move(row),
        "adds": ", ".join(a["name"] for a in row["adds"]),
        "drops": ", ".join(d["name"] for d in row["drops"]),
        "faab": row["faab"], "priority": row.get("priority", 0),
        "text": (row.get("rationale") or {}).get("text"),
        "questionable": (row.get("rationale") or {}).get("kind") == "questionable",
        "rank_shift": row.get("rank_shift"),
        "outcome": row.get("outcome"),
        # What actually happened afterwards, as its own line. The rationale
        # above is what the roster said at the time and is never rewritten
        # with hindsight.
        "aged": row.get("aged_line"),
    }


def moves_that_mattered(storage: Storage, league: League, *, tx_rows: list[dict],
                        snaps: list[dict], names: dict[int, str],
                        render_md) -> list[dict]:
    """Editorial selections from published issues, one group per issue.

    A group is `{"issue_label", "week", "moves": [...]}` with each move a
    `move_unit` plus the Commissioner's note, if he left one. A move he
    selected that the synced log no longer contains is omitted rather than
    reconstructed from the issue's wording.

    Only published issues count. A selection made for an issue that has not
    gone out is not public yet, and this page never publishes anything on
    its own.
    """
    by_id = {story_candidate_id(r): r for r in tx_rows}
    notes_by_season: dict[str, dict[str, dict]] = {}
    groups = []
    for snap in snaps:
        key = snap.get("issue_key") or ""
        if not key.startswith("week-"):
            continue
        season = snap["season"]
        if season not in notes_by_season:
            notes_by_season[season] = storage.force_flow_notes(league.slug, season)
        decisions = storage.get_story_decisions(league.slug, season, key)
        moves = []
        for cid, d in decisions.items():
            if d.get("decision") != "include" or not cid.startswith("txn:"):
                continue
            row = by_id.get(cid)
            if row is None:
                continue
            unit = move_unit(row, names)
            note = (notes_by_season[season].get(unit["txn_id"]) or {}).get("note") \
                or d.get("note")
            unit["note"] = note or None
            moves.append(unit)
        moves.sort(key=lambda m: -m["priority"])
        group = {"issue_label": f"{season} {snap['issue_label']}",
                 "week": int(key.removeprefix("week-")),
                 "moves": moves, "html": None}
        if not moves:
            prose = next((s for s in snap["sections"]
                          if s["module_key"] in PERSISTENT_TAB_MODULES), None)
            if prose is None:
                continue
            group["html"] = render_md(prose["content_md"])
        groups.append(group)
    return groups
