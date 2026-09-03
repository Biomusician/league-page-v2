"""Read the Commissioner's ranking back out of the issue he published.

The Peer and Near-Peer page has always been able to show his ranking beside
the model's and print the gap between them. It has never actually done it,
because the ranking is written as prose in the Draft Issue and nothing ever
put it in the `power_rankings` table. So the page showed the model board and
the most interesting argument on the site sat two clicks away, unconnected.

This reads the order he already published. That is not inventing a ranking:
he wrote "1. The Dude Abides", "2. Statistical Anomalies", and the numbers
are his. The parser is deliberately unforgiving about it -- a section only
counts as a ranking when the numbering is complete, unique, starts at one,
and covers most of the league. Anything less and there is no ranking here,
which is a different thing from a ranking we half-read.

The `power_rankings` table still wins when it has rows: a ranking he typed
into the Desk is a deliberate act, and this is a fallback for the ordinary
case where he wrote the newsletter instead.
"""
from __future__ import annotations

import re

from leaguepage.pubqa import QAContext, _resolve_team_heading

_HEADING_RE = re.compile(r"^[ \t]{0,3}(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$", re.M)
# "1." / "1)" / "#1" at the head of a heading. The rank has to be the first
# thing in it, so "Week 3 Notes" is never read as rank 3.
_RANKED_RE = re.compile(r"^\s*#?(\d{1,2})\s*[.)\-:]\s*(.+)$")
# A correction that recomputes the order publishes the new one as a table,
# and the numbered headings above it are the order it superseded. Reading
# the headings had the site presenting a retracted ranking as his judgment
# on the same page as the retraction.
_CORRECTION_RE = re.compile(
    r"^#{1,6}[ \t]+.*\bcorrection\b.*$", re.I | re.M)
_TABLE_ROW_RE = re.compile(r"^\s*\|\s*(\d{1,2})\s*\|\s*([^|]+?)\s*\|", re.M)

# Below this share of the league it is a top-five list, not a ranking.
MIN_COVERAGE = 0.75


def extract_ranking(sections: list[dict], *, league_slug: str, season: str,
                    issue_key: str, name_tokens: dict[int, set[str]],
                    public_names: dict[int, str]) -> dict | None:
    """The published ranking, or None.

    Returns {"section_title", "anchor", "rows": [{rank, roster_id, name}]}.
    """
    ctx = QAContext(league_slug=league_slug, season=season, issue_key=issue_key,
                    n_teams=len(public_names))
    ctx.name_tokens = name_tokens
    ctx.public_names = public_names
    best = None
    for section in sections:
        md = section.get("content_md") or ""
        rows = _corrected_rows(md, ctx) or _ranked_rows(md, ctx)
        if not _is_a_ranking(rows, len(public_names)):
            continue
        candidate = {
            "corrected": bool(_CORRECTION_RE.search(md)) and bool(_corrected_rows(md, ctx)),
            "section_title": section.get("title") or "",
            "anchor": section.get("anchor") or section.get("module_key") or "",
            "rows": [{"rank": rank, "roster_id": rid,
                      "name": public_names.get(rid) or f"Roster {rid}"}
                     for rank, rid in sorted(rows.items())],
        }
        # Prefer the most complete ranking in the issue, so a "top five" aside
        # never displaces the real one.
        if best is None or len(candidate["rows"]) > len(best["rows"]):
            best = candidate
    return best


def _corrected_rows(content_md: str, ctx: QAContext) -> dict[int, int]:
    """The order a published correction recomputed, if there is one.

    Only the text below the correction heading is read, so the original
    numbered headings above it cannot leak back in.
    """
    m = _CORRECTION_RE.search(content_md)
    if not m:
        return {}
    out: dict[int, int] = {}
    seen: set[int] = set()
    for row in _TABLE_ROW_RE.finditer(content_md[m.end():]):
        rank = int(row.group(1))
        rid, _foreign = _resolve_team_heading(row.group(2), ctx)
        if rid is None or rank in out or rid in seen:
            continue
        out[rank] = rid
        seen.add(rid)
    return out


def _ranked_rows(content_md: str, ctx: QAContext) -> dict[int, int]:
    """rank -> roster_id, for headings that carry both."""
    out: dict[int, int] = {}
    seen_rosters: set[int] = set()
    for m in _HEADING_RE.finditer(content_md):
        ranked = _RANKED_RE.match(m.group(2).strip())
        if not ranked:
            continue
        rank = int(ranked.group(1))
        rid, _foreign = _resolve_team_heading(ranked.group(2), ctx)
        if rid is None or rank in out or rid in seen_rosters:
            # A repeated rank or a repeated team means we are misreading the
            # section, and a half-read ranking is worse than none.
            continue
        out[rank] = rid
        seen_rosters.add(rid)
    return out


def _is_a_ranking(rows: dict[int, int], n_teams: int) -> bool:
    if not rows or not n_teams:
        return False
    ranks = sorted(rows)
    if ranks[0] != 1 or ranks != list(range(1, len(ranks) + 1)):
        return False
    return len(rows) >= max(3, round(MIN_COVERAGE * n_teams))


def disagreements(rows: list[dict], *, top: int = 3) -> tuple[list[dict], int]:
    """The widest gaps between the two rankings, and the floor that was used.

    This is the payoff: a model that agrees everywhere is a table, and the
    places it does not are an argument.

    An undisclosed top-N cut is not fair to the teams it excludes -- a team
    three places apart being left out while two other three-place gaps are
    shown reads as the site choosing whose disagreement counts. So the cut
    is a gap FLOOR, every team at or above it is listed, and the page states
    the floor.
    """
    scored = [r for r in rows if r.get("model_gap")]
    if not scored:
        return [], 0
    scored.sort(key=lambda r: (-abs(r["model_gap"]), r["rank"]))
    floor = abs(scored[min(top, len(scored)) - 1]["model_gap"])
    out = []
    for r in scored:
        gap = abs(r["model_gap"])
        if gap < floor:
            break
        out.append({
            "name": r["name"],
            "slug": r.get("slug"),
            "rank": r["rank"],
            "model_rank": r["model_rank"],
            "gap": gap,
            # A statement about the two positions, not a verdict on either
            # ranking. The board has no opinion, and its copy should not
            # sound like it does.
            "line": (f"He has them #{r['rank']}; roster construction alone "
                     f"puts them #{r['model_rank']}."),
        })
    return out, floor
