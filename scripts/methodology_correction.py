"""Publish a methodology correction to a published Draft Issue.

The 2026 Draft Power Rankings scored each team as the sum of every pick's
deviation from the consensus board. The calibration decision that followed
(docs/DECISIONS.md, 2026-08-30) established that overall ECR ranks every
kicker and defense below the draftable range while lineups force everybody
to draft them, so those deltas measure the reference board's shape rather
than a roster decision.

This script re-runs the ranking under the corrected rule and, when the order
materially changes, APPENDS a labelled correction to the published section.
It never rewrites the Commissioner's per-team capsules: those are his
analysis of each roster's design and remain true. What changes is the
arithmetic that ordered them, and the correction says so in plain terms with
the numbers recomputed from the stored reference board.

    scripts/methodology_correction.py --league surfeit          # show it
    scripts/methodology_correction.py --league surfeit --apply  # publish it
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from leaguepage.adp import load_adp_for_league
from leaguepage.config import EDITORIAL_DIR, PUBLISHED_DIR, get_league
from leaguepage.draft_analysis import analyze_league_draft
from leaguepage.draft_value import market_value_ranking
from leaguepage.publish import PublishError, revise_issue, snapshot_family
from leaguepage.storage import Storage
from leaguepage.team_names import resolve_public_names

MARKER = "## Correction — ranking methodology"


def build_note(ranking: dict, *, board: str) -> str:
    """The correction, rendered from computed values only.

    Deliberately not in the Commissioner's voice: this is a corrections
    notice, and the house rule is that machine copy never imitates him."""
    rows = ranking["rows"]
    share = f"{ranking['special_teams_share']:.0%}"
    lines = [
        MARKER,
        "",
        "*Added after publication. The capsules below the original ranking are "
        "unchanged; what follows corrects the arithmetic that ordered them.*",
        "",
        "The ranking above scored each team as the sum of every pick's "
        "deviation from the consensus board, kickers and defenses included. "
        f"On this board that was the wrong denominator: **{share} of all the "
        "deviation in this draft came from special teams**. Overall consensus "
        "ranks every kicker and defense below the draftable range while the "
        "lineup forces all ten teams to draft them, so those numbers measure "
        "the reference board's shape, not a roster decision. Nobody chose to "
        "pay that tax and nobody could avoid it.",
        "",
        "Recomputed on skill positions only (QB/RB/WR/TE), against the same "
        f"stored {board} board, the order changes for "
        f"**{ranking['teams_moved']} of {len(rows)} teams**, the largest move "
        f"being {ranking['largest_move']} places:",
        "",
        "| # | Team | Skill-position value | Special-teams tax | Was |",
        "| --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        move = ("—" if not r["movement"]
                else f"#{r['raw_rank']} ({r['movement']:+d})")
        lines.append(f"| {r['skill_rank']} | {r['name']} | {r['skill']:+.0f} | "
                     f"{r['special_teams']:+.0f} | {move} |")
    lines += [
        "",
        "Special-teams value is shown beside each team rather than folded in, "
        "the same treatment the Draft page gives its headline Reaches and "
        "Steals. Nothing about any individual pick's REACH or STEAL "
        "classification changes; those compare one selection with the board "
        "and are unaffected.",
    ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--league", required=True)
    ap.add_argument("--issue", default="draft")
    ap.add_argument("--section", default="custom")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    league = get_league(args.league)
    with Storage() as s:
        season = str((s.get_league(league.league_id) or {}).get("season") or "")
        names = {rid: v["name"] for rid, v in resolve_public_names(s, league).items()}
        adp = load_adp_for_league(league)
        analysis = analyze_league_draft(s, league, managers={}, adp=adp)
    if not analysis:
        print("no draft analysis available")
        return 1

    ranking = market_value_ranking(analysis["teams"], names)
    board = str(analysis.get("adp_provenance") or "consensus")
    print(f"{league.slug}: special-teams share {ranking['special_teams_share']:.0%}, "
          f"{ranking['teams_moved']} team(s) move, largest {ranking['largest_move']} "
          f"places -> material={ranking['material']}")
    if not ranking["material"]:
        print("The published order does not materially change; no correction needed.\n"
              "(The explanation is still worth checking, but there is nothing to "
              "recompute.)")
        return 0

    family = snapshot_family(PUBLISHED_DIR, league.slug, season, args.issue)
    if not family:
        print("nothing published for this issue")
        return 1
    latest = json.loads(family[-1].read_text(encoding="utf-8"))
    sections = [dict(sec) for sec in latest["sections"]]
    target = next((sec for sec in sections if sec["module_key"] == args.section), None)
    if target is None:
        print(f"no section '{args.section}' in the published issue")
        return 1
    if MARKER in target["content_md"]:
        print("This issue already carries a methodology correction.")
        return 0

    note = build_note(ranking, board=board)
    print("\n" + note + "\n")
    if not args.apply:
        print("dry run. Re-run with --apply to publish the correction.")
        return 0

    target["content_md"] = target["content_md"].rstrip() + "\n\n" + note + "\n"
    # Keep the editorial source in step so a republish carries the correction.
    src = EDITORIAL_DIR / season / league.slug / args.issue / "sections" / f"{args.section}.md"
    if src.exists():
        src.write_text(src.read_text(encoding="utf-8").rstrip() + "\n\n" + note + "\n",
                       encoding="utf-8")
        print(f"updated source {src}")

    with Storage() as s:
        try:
            out = revise_issue(
                s, league, season, args.issue, sections=sections,
                note="methodology correction: ranking recomputed on skill "
                     "positions; special-teams deltas reported separately")
        except PublishError as exc:
            print(f"\nCorrection refused: {exc}")
            return 1
    print(f"\ncorrection written: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
