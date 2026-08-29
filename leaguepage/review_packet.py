"""Commissioner Review Packet — the whole issue's decisions on one screen.

Everything here is SYSTEM RECOMMENDATION, clearly labeled; nothing in this
packet writes a decision. The distinction is strict:
    system recommendation  -> this file / the review screen
    commissioner selection -> the boards (stories/awards/rankings/lowdown)
    commissioner approval  -> module approval + publish gates
"""
from __future__ import annotations

import re
from pathlib import Path

from leaguepage.config import League
from leaguepage.issue_builder import issue_dir
from leaguepage.storage import Storage
from leaguepage.team_names import resolve_public_names

# Editorial name suggestions for still-unnamed rosters (coined during drafting;
# purely suggestions — the commissioner confirms or replaces them on the Desk).
NAME_SUGGESTIONS = {
    ("surfeit", 2): "The Chicago Syndicate",
    ("surfeit", 4): "The No-DEF Department",
    ("surfeit", 5): "Goff Season",
    ("surfeit", 6): "The Motor City Annex",
    ("surfeit", 10): "The Discount Warehouse",
}


def _take_candidates(idir: Path) -> list[str]:
    out = []
    for path in list((idir / "lowdown").glob("*.md")) + list((idir / "sections").glob("*.md")):
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*TAKE CANDIDATE:\s*(.+)", line)
            if m:
                out.append(f"{m.group(1).strip()}  (from {path.name})")
    return out


def build_review_packet(
    storage: Storage,
    league: League,
    season: str,
    issue_key: str,
    *,
    awards: list[dict],
    candidates: list[dict],
    base_dir: Path | None = None,
) -> Path:
    idir = issue_dir(league, season, issue_key, base_dir)
    names = resolve_public_names(storage, league)
    award_decisions = storage.get_award_decisions(league.slug, season, issue_key)
    story_decisions = storage.get_story_decisions(league.slug, season, issue_key)
    lines: list[str] = [
        f"# Commissioner Review Packet — {league.display_name} {season} {issue_key}",
        "",
        "Everything below is SYSTEM RECOMMENDATION with evidence. Nothing is",
        "selected or approved until you do it on the boards. One screen, every",
        "decision; the boards hold the detail.",
        "",
    ]
    a = lines.append

    # --- unresolved names first: they block everything ---
    unresolved = [rid for rid, v in names.items() if v["name"] is None]
    if unresolved:
        a("## BLOCKING — unresolved public team names")
        a("")
        a("Publication is blocked until every roster has a confirmed public name.")
        a("Confirm on the workspace Team Names panel. Suggestions below are")
        a("editorial inventions from the draft coverage; adopt, change, or ignore.")
        for rid in unresolved:
            suggestion = NAME_SUGGESTIONS.get((league.slug, rid))
            a(f"- Roster {rid}: no Sleeper team name set."
              + (f" Suggested: \"{suggestion}\"" if suggestion else " No obvious suggestion."))
        a("")

    # --- Lowdown ---
    themes = (idir / "lowdown" / "themes.md")
    rough = (idir / "lowdown" / "rough-lowdown.md")
    a("## LOWDOWN")
    a("")
    if themes.exists():
        a("System recommendation: **Assumption-Based Planning** (theme 1).")
        a("Why: fits the draft evidence already gathered; seeds False Assumptions")
        a("naturally; broad enough for 2035 Futures humor beyond procurement; the")
        a("strongest existing rough draft already uses it.")
        a("Alternates in lowdown/themes.md: first exercise after a reorg;")
        a("interwar rearmament.")
    else:
        a("No themes generated yet — run the workspace Build, then the Claude Code steps.")
    a(f"Rough draft: {'present, awaiting your edit (lowdown screen)' if rough.exists() else 'not yet requested'}.")
    a("")

    # --- Awards ---
    a("## HARDWARE — system-recommended winners")
    a("")
    for aw in awards:
        if not aw["nominees"]:
            continue
        top = aw["nominees"][0]
        who = top.get("player") or top.get("team_slug")
        decided = award_decisions.get(aw["award_key"])
        status = (f"DECIDED: {decided['decision']} {decided.get('winner') or ''}".strip()
                  if decided else "awaiting your decision")
        a(f"### {aw['award_name']} — recommend: {who}  [{status}]")
        a(f"- basis: {aw['metric']}")
        for f in top.get("facts", [])[:2]:
            a(f"- {f}")
        runners = [n.get("player") or n.get("team_slug") for n in aw["nominees"][1:3]]
        if runners:
            a(f"- runners-up: {', '.join(runners)}")
        a("")

    # --- Capsules ---
    capsules = idir / "sections" / "draft-capsules.md"
    a("## TEAM CAPSULES")
    a("")
    if capsules.exists():
        text = capsules.read_text(encoding="utf-8")
        count = text.count("### ")
        pending = text.count("(name pending)")
        a(f"- {count} capsules drafted (rough, awaiting your edit).")
        if pending:
            a(f"- {pending} reference still-unnamed rosters; their prose updates "
              "naturally once names are confirmed.")
        a("- Voice pass done: frames span assumptions, campaign design, portfolios,")
        a("  divestment, redundancy, scenario design, mission command, alliances,")
        a("  branch planning, and cost imposition (procurement only where apt).")
    else:
        a("- Not yet drafted.")
    a("")

    # --- Rankings ---
    a("## PEER AND NEAR-PEER COMPETITION")
    a("")
    a("No ranking is set. The ordering below is a SYSTEM RECOMMENDATION built")
    a("from one evidence stream (net skill-position value vs reference ranks,")
    a("i.e. the Draft Crusher metric). It is a starting point for your editorial")
    a("judgment, not an objective ranking; set yours on the rankings screen.")
    crusher = next((aw for aw in awards if aw["award_key"] == "draft-crusher"), None)
    if crusher:
        for i, n in enumerate(crusher["nominees"], start=1):
            a(f"{i}. {n['team_slug']} (net delta {n.get('metric_value')})")
        a("(remaining teams: no strong signal either way from this metric)")
    a("")

    # --- Takes ---
    takes = _take_candidates(idir)
    tracked = storage.all_takes(league.slug, season)
    a("## TAKES WORTH TRACKING")
    a("")
    if takes:
        a("Flagged in the rough drafts (track via the draft-review or story boards):")
        for t in takes[:10]:
            a(f"- {t}")
    if tracked:
        a("Already tracked:")
        for t in tracked[:10]:
            a(f"- take:{t['take_id']} [{t['status']}] \"{t['quote']}\"")
    if not takes and not tracked:
        a("- none flagged yet")
    a("")

    # --- Stories ---
    included = [cid for cid, d in story_decisions.items() if d["decision"] == "include"]
    a("## STORY BOARD")
    a("")
    a(f"- {len(candidates)} candidates surfaced; {len(included)} currently included.")
    top5 = sorted(candidates, key=lambda c: -(c.get("score") or 0))[:5]
    for c in top5:
        a(f"- [{c.get('score', '—')}] {c['headline']}")
    a("")
    a("Full detail and controls: the stories board.")
    a("")

    path = idir / "REVIEW_PACKET.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
