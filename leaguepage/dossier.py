"""Deterministic draft dossiers — the factual base Claude Code writes from.

One markdown file per team plus a league-level dossier, written under
editorial/<season>/<league>/draft/dossiers/. Every number in a dossier comes
from draft_analysis (with its ADP provenance) or from CONFIRMED editorial
metadata; unverified material is excluded or explicitly labeled.
"""
from __future__ import annotations

from pathlib import Path

from leaguepage.config import EDITORIAL_DIR
from leaguepage.editorial import confirmed_aliases, confirmed_identity_facts


def _fmt_delta(p: dict) -> str:
    return (f"{p['name']} ({p['position']}) — pick #{p['pick_no']}, reference {p['adp']:g}, "
            f"delta {p['delta']:+g}")


def team_dossier_md(
    analysis: dict,
    team: dict,
    candidates: list[dict],
    managers: dict[str, dict],
) -> str:
    src = analysis.get("adp_provenance") or "no reference-rank source configured"
    lines: list[str] = []
    a = lines.append

    a(f"# Draft Dossier — {team['team_name'] or team['team_slug']}")
    a("")
    a(f"League: {analysis['league']} {analysis['season']} · Draft {analysis['draft_id']} "
      f"({analysis['draft_status']}, {analysis['pick_count']} picks)")
    a(f"Reference ranks: {src}")
    a("")

    a("## Team")
    who = ", ".join(team["manager_display_names"]) or "unknown manager"
    a(f"- Manager{'s' if team['co_managed'] else ''}: {who}"
      + (" (co-managed)" if team["co_managed"] else ""))
    for key in team["manager_keys"]:
        m = managers.get(key) or {}
        facts = confirmed_identity_facts(m)
        aliases = confirmed_aliases(m)
        if aliases:
            a(f"- {m.get('display_name', key)}: confirmed aliases {', '.join(aliases)}")
        for fname, fval in facts.items():
            if fname != "notes":
                a(f"- {m.get('display_name', key)} {fname}: {fval}")
        if facts.get("notes"):
            a(f"- Lore ({m.get('display_name', key)}): {facts['notes']}")
    a("")

    a("## Draft summary")
    counts = ", ".join(f"{n} {pos}" for pos, n in team["position_counts"].items())
    a(f"- {team['pick_count']} picks: {counts}")
    from leaguepage.matchup_interest import format_position_mix
    a(f"- Rounds 1-3 mix: {format_position_mix(team['early_rounds_positions'])}")
    a(f"- Bench-range mix ({team['bench_range_definition']}): "
      f"{format_position_mix(team['bench_range_positions'])}")
    for pos, first in sorted(team["first_pick_by_position"].items()):
        a(f"- First {pos}: {first['name']} (round {first['round']}, pick #{first['pick_no']})")
    a("")

    a("## Picks")
    for p in team["picks_by_round"]:
        delta = f", ref {p['adp']:g}, delta {p['delta']:+g}" if p["delta"] is not None else ""
        a(f"- R{p['round']} #{p['pick_no']}: {p['name']} ({p['position']}, {p['nfl_team'] or 'FA'}){delta}")
    a("")

    a("## Reference-rank movement")
    if team["biggest_reach"] or team["biggest_value"]:
        if team["biggest_reach"]:
            a(f"- Largest reach: {_fmt_delta(team['biggest_reach'])} — source: {src}")
        if team["biggest_value"]:
            a(f"- Largest value: {_fmt_delta(team['biggest_value'])} — source: {src}")
        for label, pool in (("Reaches", team["reaches"]), ("Values", team["values"])):
            if pool:
                a(f"- {label}: " + "; ".join(_fmt_delta(p) for p in pool[:3]))
    else:
        a("- No reference-rank deltas available for this team.")
    a("")

    a("## Structure notes (deterministic)")
    for s in team["stacks"]:
        a(f"- Stack: {s['qb']} + {', '.join(s['partners'])} ({s['nfl_team']})")
    for c in team["nfl_team_concentration"]:
        a(f"- {c['count']} players from {c['nfl_team']}: {', '.join(c['players'])}")
    for an in team["anomalies"]:
        a(f"- {an['fact']}")
    if not (team["stacks"] or team["nfl_team_concentration"] or team["anomalies"]):
        a("- Nothing unusual detected by rule-based checks.")
    a("")

    a("## Story hooks (rule-based candidates)")
    mine = [c for c in candidates if team["team_slug"] in c["teams"]]
    if mine:
        for c in mine:
            a(f"- [{c['category']}, score {c['score']}] {c['headline']}")
            a(f"  - why surfaced: {c['why']}")
            for f in c["facts"]:
                a(f"  - {f}")
    else:
        a("- No rule-based candidates surfaced for this team.")
    a("")

    a("## Archive callbacks")
    callbacks = [c for c in mine if c["category"] == "archive-callback"]
    if callbacks:
        for c in callbacks:
            for f in c["facts"]:
                a(f"- {f}")
    else:
        a("- None found via confirmed aliases/team names.")
    a("")

    a("## Evidence")
    a("Machine-readable references backing the facts above (scheme: leaguepage/evidence.py):")
    a("```")
    seen = set()
    for ref in team["evidence"]:
        seen.add(ref)
    for c in mine:
        for ref in c["evidence"]:
            seen.add(ref)
    for ref in sorted(seen):
        a(ref)
    a("```")
    a("")
    return "\n".join(lines)


def league_dossier_md(analysis: dict, candidates: list[dict], awards: list[dict]) -> str:
    src = analysis.get("adp_provenance") or "no reference-rank source configured"
    lines: list[str] = []
    a = lines.append
    a(f"# League Draft Dossier — {analysis['league_name']} ({analysis['season']})")
    a("")
    a(f"- Draft {analysis['draft_id']}: {analysis['draft_status']}, "
      f"{analysis['pick_count']}/{analysis.get('expected_pick_count') or '?'} picks, "
      f"{analysis['total_teams']} teams, {analysis['rounds']} rounds ({analysis['draft_type']}).")
    a(f"- Roster: {' '.join(analysis['roster_positions'])}")
    a(f"- Reference ranks: {src}")
    for w in analysis["warnings"]:
        a(f"- WARNING: {w}")
    a("")
    a("## League-wide reference-rank movement")
    for label, pool in (("Biggest reaches", analysis["league_biggest_reaches"]),
                        ("Biggest values", analysis["league_biggest_values"])):
        a(f"### {label}")
        if pool:
            for p in pool:
                a(f"- {p['name']} ({p['team_slug']}): pick #{p['pick_no']}, "
                  f"reference {p['adp']:g}, delta {p['delta']:+g}")
        else:
            a("- None available.")
    a("")
    a("## Teams at a glance")
    for t in analysis["teams"]:
        counts = ", ".join(f"{n} {pos}" for pos, n in t["position_counts"].items())
        a(f"- **{t['team_slug']}** ({', '.join(t['manager_display_names'])}): {counts}")
    a("")
    a("## Top story candidates")
    for c in candidates[:12]:
        a(f"- [{c['category']}, score {c['score']}] {c['headline']}")
    a("")
    a("## Award nomination summary")
    for aw in awards:
        noms = ", ".join(n.get("player") or n["team_slug"] for n in aw["nominees"][:3])
        a(f"- {aw['award_name']} ({aw['kind']}): {noms}")
    a("")
    return "\n".join(lines)


def write_dossiers(
    analysis: dict,
    candidates: list[dict],
    awards: list[dict],
    managers: dict[str, dict],
    base_dir: Path | None = None,
) -> list[Path]:
    """Write per-team + league dossiers; returns written paths. Idempotent."""
    root = (base_dir or EDITORIAL_DIR) / analysis["season"] / analysis["league"] / "draft" / "dossiers"
    root.mkdir(parents=True, exist_ok=True)
    written = []
    for team in analysis["teams"]:
        path = root / f"{team['team_slug']}.md"
        path.write_text(team_dossier_md(analysis, team, candidates, managers), encoding="utf-8")
        written.append(path)
    league_path = root / "_league.md"
    league_path.write_text(league_dossier_md(analysis, candidates, awards), encoding="utf-8")
    written.append(league_path)
    return written
