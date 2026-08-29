"""Editorial packet builder — the V1 Claude Code integration.

Emits a self-contained authoring-context directory:

    editorial/<season>/<league>/draft/generated/
        MANIFEST.json           build metadata (only file with a timestamp)
        AUTHORING_BRIEF.md      what the author may and may not infer
        data.json               draft/team facts (machine-readable)
        analytics.json          full deterministic analysis with evidence ids
        story_candidates.md     scored candidates + commissioner decisions
        award_nominations.md    nominees + commissioner decisions
        commissioner_decisions.md  power rankings, notes — commissioner-supplied
        team_dossiers/          per-team + league dossiers
        archive_context.md      the ALLOWED callback set (quotes with sources)
        manager_context.md      confirmed lore only; unverified listed as banned
        takes.md                tracked takes (verbatim wording + status)

Everything except MANIFEST.json is deterministic for identical inputs, so
reruns are idempotent and diffs are meaningful.
"""
from __future__ import annotations

import json
from pathlib import Path

from leaguepage.adp import load_adp_for_league
from leaguepage.config import EDITORIAL_DIR, League
from leaguepage.dossier import league_dossier_md, team_dossier_md
from leaguepage.draft_analysis import analyze_league_draft
from leaguepage.draft_awards import draft_award_nominations
from leaguepage.draft_stories import draft_story_candidates
from leaguepage.editorial import (
    confirmed_aliases,
    confirmed_coalition_mappings,
    confirmed_identity_facts,
    load_coalitions,
    load_managers,
)
from leaguepage.storage import Storage, utcnow_iso

WRITING_SKILL = ".claude/skills/my-writing-style/SKILL.md"
ARCHIVE_STYLE_NOTES = "editorial/style/ARCHIVE_STYLE_NOTES.md"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _story_candidates_md(candidates: list[dict], decisions: dict[str, dict]) -> str:
    lines = ["# Story candidates (rule-based; commissioner decisions applied)", ""]
    buckets = {"include": [], "save": [], "undecided": [], "ignore": []}
    for c in candidates:
        d = decisions.get(c["candidate_id"])
        buckets[d["decision"] if d else "undecided"].append((c, d))
    for bucket, label in (("include", "INCLUDED — build the issue around these"),
                          ("save", "SAVED — usable if they fit"),
                          ("undecided", "UNDECIDED — commissioner has not ruled"),
                          ("ignore", "IGNORED — do not use")):
        lines.append(f"## {label}")
        if not buckets[bucket]:
            lines.append("(none)")
        for c, d in buckets[bucket]:
            lines.append(f"### [{c['score']}] {c['headline']}")
            lines.append(f"- category: {c['category']} · candidate_id: `{c['candidate_id']}`")
            lines.append(f"- why surfaced: {c['why']}")
            for f in c["facts"]:
                lines.append(f"- fact: {f}")
            if d and d.get("note"):
                lines.append(f"- COMMISSIONER NOTE: {d['note']}")
            lines.append(f"- evidence: {', '.join('`' + e + '`' for e in c['evidence'])}")
            lines.append("")
        lines.append("")
    return "\n".join(lines)


def _awards_md(awards: list[dict], decisions: dict[str, dict]) -> str:
    lines = ["# Award nominations (never automatic winners)", ""]
    for aw in awards:
        d = decisions.get(aw["award_key"])
        lines.append(f"## {aw['award_name']}  ({aw['kind']})")
        lines.append(f"- metric: {aw['metric']}")
        if d:
            verdict = {"awarded": "AWARDED", "manual": "MANUAL WINNER", "rejected": "NO AWARD"}[d["decision"]]
            lines.append(f"- COMMISSIONER DECISION: {verdict}"
                         + (f" — {d['winner']}" if d.get("winner") else "")
                         + (f" ({d['note']})" if d.get("note") else ""))
        else:
            lines.append("- COMMISSIONER DECISION: none yet — do not write award copy for this one.")
        for n in aw["nominees"]:
            who = n.get("player") or n["team_slug"]
            mv = f" · metric {n['metric_value']}" if n.get("metric_value") is not None else ""
            lines.append(f"- nominee: {who}{mv}")
            for f in n["facts"]:
                lines.append(f"  - {f}")
        lines.append("")
    return "\n".join(lines)


def _decisions_md(power: list[dict], analysis: dict) -> str:
    lines = ["# Commissioner-supplied decisions and rankings", "",
             "Everything in this file is commissioner-authored editorial judgment —",
             "it may be stated in the issue as the commissioner's position.", "",
             "## Peer and Near-Peer Competition — preseason baseline"]
    if power:
        by_roster = {t["roster_id"]: t for t in analysis.get("teams", [])}
        tiers = {1: "Peer Competition", 2: "Near-Peer Competition",
                 3: "Competitive but Flawed", 4: "Strategic Reassessment Required"}
        for p in power:
            t = by_roster.get(p["roster_id"], {})
            name = t.get("team_name") or t.get("team_slug") or f"roster {p['roster_id']}"
            tier = f" · Tier {p['tier']} ({tiers.get(p['tier'], '?')})" if p.get("tier") else ""
            note = f" — {p['note']}" if p.get("note") else ""
            lines.append(f"- #{p['rank']} {name}{tier}{note}")
    else:
        lines.append("(not set yet — the issue cannot include a preseason ranking section)")
    lines.append("")
    return "\n".join(lines)


def _archive_context_md(analysis: dict, storage: Storage, managers: dict[str, dict]) -> str:
    from leaguepage.story_memory import retrieve_callbacks

    lines = [
        "# Archive context — the ALLOWED callback set",
        "",
        "These are the only archival quotations/callbacks usable in this issue.",
        "Retrieval is scoped to THIS league's own archive; cross-league entries",
        "appear only for managers Jonathan explicitly marked",
        "allow_cross_league_callbacks, and are labeled. Do not mine the archive",
        "for anything beyond this list, and never attach archive material to a",
        "manager through an unverified alias.",
        "",
    ]
    any_hits = False
    for t in analysis.get("teams", []):
        hits = retrieve_callbacks(storage, analysis["league"], [t], managers,
                                  season=analysis["season"], limit=3)
        if hits:
            any_hits = True
            lines.append(f"## {t['team_name'] or t['team_slug']} ({', '.join(t['manager_display_names'])})")
            for h in hits:
                label = f"{h['source_league']} {h['season'] or '????'}" + (f" wk{h['week']}" if h["week"] else "")
                flags = []
                if h["cross_league"]:
                    flags.append("CROSS-LEAGUE, explicitly approved — attribute to source league")
                if h["date_unreliable"]:
                    flags.append("season dating not high-confidence — no date-specific claims")
                flag_txt = f" [{'; '.join(flags)}]" if flags else ""
                lines.append(f"- matched '{h['matched_term']}' in **{h['title']}** ({label}, {h['evidence']}){flag_txt}")
                lines.append(f"  - snippet: {h['snippet']}")
            lines.append("")
    if not any_hits:
        lines.append("(no same-league archive matches via confirmed aliases/team names — "
                     "this issue gets no callbacks)")
        lines.append("")
    return "\n".join(lines)


def _manager_context_md(analysis: dict, managers: dict[str, dict], coalitions: dict) -> str:
    lines = [
        "# Manager context — confirmed editorial memory only",
        "",
        "Facts below are safe to state. The BANNED section lists inferences that",
        "exist in metadata but are NOT usable until Jonathan confirms them.",
        "",
    ]
    for t in analysis.get("teams", []):
        header = f"## {t['team_name'] or t['team_slug']} ({', '.join(t['manager_display_names'])})"
        entries = []
        for key in t.get("manager_keys", []):
            m = managers.get(key) or {}
            aliases = confirmed_aliases(m)
            facts = confirmed_identity_facts(m)
            bits = m.get("recurring_bits") or []
            retired = m.get("retired_bits") or []
            sens = m.get("sensitivity", "fair_game")
            if aliases:
                entries.append(f"- confirmed aliases: {', '.join(aliases)}")
            for k, v in facts.items():
                entries.append(f"- {k}: {v}")
            if bits:
                entries.append(f"- recurring bits: {', '.join(bits)}")
            if retired:
                entries.append(f"- RETIRED bits (do not use): {', '.join(retired)}")
            if sens != "fair_game":
                entries.append(f"- SENSITIVITY: {sens} — respect this")
        if t.get("co_managed"):
            entries.append("- co-managed team (Sleeper-confirmed)")
        if entries:
            lines.append(header)
            lines.extend(entries)
            lines.append("")

    lines.append("## Coalition identities (confirmed facts, supplied by Jonathan)")
    for name, ident in (coalitions.get("identities") or {}).items():
        if ident.get("status") != "confirmed":
            continue
        parts = [ident.get("nationality"), ident.get("role"), ident.get("aircraft")]
        lines.append(f"- {name}: " + " · ".join(p for p in parts if p)
                     + f" · coalition {ident.get('coalition')}")
    mapped = confirmed_coalition_mappings(coalitions)
    if mapped:
        for c in mapped:
            lines.append(f"- CONFIRMED roster mapping: {c['name']} = "
                         f"{c['roster_mapping']['league']} roster {c['roster_mapping']['roster_id']}")
    else:
        lines.append("- NOTE: no coalition→roster mapping is confirmed yet, so coalition "
                     "identities may NOT be attached to any specific team in prose.")
    if coalitions.get("humor_guidance"):
        lines.append(f"- humor guidance: {coalitions['humor_guidance']}")
    lines.append("")

    lines.append("## BANNED — unverified inferences (never state as fact)")
    banned = False
    for key, m in managers.items():
        for ua in m.get("unverified_aliases") or []:
            banned = True
            lines.append(f"- {m.get('display_name', key)} = '{ua['name']}' ({ua['status']}) — {ua.get('rule', 'do not use')}")
    for c in coalitions.get("coalitions", []):
        mapping = c.get("roster_mapping") or {}
        if mapping.get("status") == "inferred":
            banned = True
            lines.append(f"- {c['name']} ↔ {mapping.get('league')} roster {mapping.get('roster_id')} "
                         f"(inferred) — {mapping.get('rule', 'do not use')}")
    if not banned:
        lines.append("(none)")
    lines.append("")
    return "\n".join(lines)


def _takes_md(takes: list[dict]) -> str:
    lines = ["# Tracked takes (receipts)", "",
             "Quotes are verbatim and immutable. New substantive assertions written",
             "for this issue should be flagged for tracking in the final report.", ""]
    if not takes:
        lines.append("(none tracked yet)")
    for t in takes:
        lines.append(f"- take:{t['take_id']} [{t['status']}] \"{t['quote']}\"")
        meta = (f"  - subject {t['subject']} · {t['context'] or '?'} {t['season']}"
                + (f" · topic {t['topic']}" if t.get("topic") else "")
                + (f" · players {t['players']}" if t.get("players") else "")
                + (f" · confidence {t['confidence']}" if t.get("confidence") else ""))
        lines.append(meta)
        if t.get("resolution"):
            lines.append(f"  - later evaluation: {t['resolution']}")
    lines.append("")
    return "\n".join(lines)


def _authoring_brief_md(league: League, analysis: dict, issue_dir: str) -> str:
    return f"""# AUTHORING BRIEF — {league.display_name} {analysis['season']} Draft Issue

You are drafting newsletter copy for Jonathan (the commissioner and credited
author). He edits everything; nothing you write publishes without his approval.

## The one critical rule

**Facts come from this packet. You find the story and write the prose.**
Do not calculate, estimate, or recall any number, rank, record, or historical
event — if it isn't in this directory, it doesn't go in the draft.

## What each file is, and what it may support

| File | Provenance class | May be used as |
|---|---|---|
| data.json, analytics.json | deterministic fact | stated facts (cite naturally, e.g. "taken 131 picks ahead of where FantasyPros ranked him") |
| team_dossiers/ | deterministic fact + rule-based hooks | facts; hooks are suggestions, not facts |
| story_candidates.md | inference/editorial suggestion + commissioner decisions | INCLUDED items structure the issue; IGNORED items are off-limits |
| award_nominations.md | deterministic nominees + commissioner decisions | write copy ONLY for awards with a commissioner decision of AWARDED or MANUAL WINNER |
| commissioner_decisions.md | commissioner-supplied judgment | his positions, stated as his |
| archive_context.md | archival quotation/callback (the allowed set) | callbacks, attributed to their source issue/league |
| manager_context.md | confirmed lore + BANNED list | confirmed items only; the BANNED list is absolute |
| takes.md | receipts | reference past takes; flag new assertions as take candidates |

## Style

**Before writing anything, read `{WRITING_SKILL}` (the `my-writing-style`
skill) and follow it — it is the authoritative voice profile.** Secondary
archive observations and league theming live in `{ARCHIVE_STYLE_NOTES}`;
where they differ, the skill controls. Jonathan has explicitly requested
generated first drafts from this workflow, so draft in full. League theme:
**{league.display_name}** — {league.subtitle}. Section labels are canonical
(Common Tactical Picture, Peer and Near-Peer Competition, etc.). Before
finishing, sweep per the skill for em-dashes and every form of the
negated-parallel contrast; `scripts/style_check.py <file>` catches the
mechanical cases.

## You may

- Choose angles, structure, jokes, and framing.
- Use any fact in the packet, phrased with its provenance intact.
- Write conditional/forward-looking opinion clearly voiced as opinion,
  and mark substantive ones as `TAKE CANDIDATE:` for Jonathan to track.

## You may not

- Introduce any fact not in the packet (stats, history, injuries, ADP).
- Use anything from manager_context.md's BANNED list, or attach coalition
  identities to specific teams while no roster mapping is confirmed.
- Turn a reference-rank delta into a verdict ("terrible pick") — the delta is
  the fact; the judgment is editorial and must read as opinion.
- Present the preseason ranking as objective; it is the commissioner's.
- Write award copy for undecided/rejected awards.
- Auto-publish anything. Output goes to `{issue_dir}/draft-issue.md` as a
  ROUGH DRAFT — COMMISSIONER EDIT REQUIRED.

## Deliverable

Write `draft-issue.md` in this packet's parent directory
(`{issue_dir}/`), containing the Draft Issue in Markdown with the canonical
section labels, ready for Jonathan's edit. Start the file with:

    <!-- ROUGH DRAFT - COMMISSIONER EDIT REQUIRED -->

Then, in your session summary (not the file), list: facts used that felt
thin, any TAKE CANDIDATE lines, and anything the packet was missing.
"""


def build_draft_packet(
    storage: Storage,
    league: League,
    *,
    base_dir: Path | None = None,
) -> Path | None:
    """Build the complete draft editorial packet. Returns the packet dir,
    or None when the league has no draft data at all."""
    managers = load_managers()
    coalitions = load_coalitions()
    adp = load_adp_for_league(league)
    analysis = analyze_league_draft(storage, league, managers=managers, adp=adp)
    if analysis is None:
        return None
    season = analysis["season"]
    candidates = draft_story_candidates(analysis, storage=storage, managers=managers, coalitions=coalitions)
    awards = draft_award_nominations(analysis)
    story_decisions = storage.get_story_decisions(league.slug, season, "draft")
    award_decisions = storage.get_award_decisions(league.slug, season, "draft")
    power = storage.get_power_rankings(league.slug, season, "preseason")
    takes = storage.all_takes(league.slug, season)

    issue_dir = (base_dir or EDITORIAL_DIR) / season / league.slug / "draft"
    out = issue_dir / "generated"

    _write(out / "data.json", json.dumps({
        "league": league.slug,
        "league_name": analysis["league_name"],
        "season": season,
        "draft": {k: analysis[k] for k in (
            "draft_id", "draft_status", "draft_type", "rounds", "total_teams",
            "pick_count", "expected_pick_count", "roster_positions")},
        "adp_source": analysis["adp_source"],
        "adp_provenance": analysis["adp_provenance"],
        "teams": [
            {k: t[k] for k in (
                "roster_id", "team_slug", "team_name", "manager_keys",
                "manager_display_names", "co_managed", "position_counts")}
            for t in analysis["teams"]
        ],
    }, indent=1, ensure_ascii=False))
    _write(out / "analytics.json", json.dumps(analysis, indent=1, ensure_ascii=False))
    _write(out / "story_candidates.md", _story_candidates_md(candidates, story_decisions))
    _write(out / "award_nominations.md", _awards_md(awards, award_decisions))
    _write(out / "commissioner_decisions.md", _decisions_md(power, analysis))
    _write(out / "archive_context.md", _archive_context_md(analysis, storage, managers))
    _write(out / "manager_context.md", _manager_context_md(analysis, managers, coalitions))
    _write(out / "takes.md", _takes_md(takes))
    for team in analysis["teams"]:
        _write(out / "team_dossiers" / f"{team['team_slug']}.md",
               team_dossier_md(analysis, team, candidates, managers))
    _write(out / "team_dossiers" / "_league.md",
           league_dossier_md(analysis, candidates, awards))
    _write(out / "AUTHORING_BRIEF.md",
           _authoring_brief_md(league, analysis, issue_dir.as_posix()))
    _write(out / "MANIFEST.json", json.dumps({
        "generated_at": utcnow_iso(),
        "league": league.slug,
        "season": season,
        "workflow": "draft",
        "draft_id": analysis["draft_id"],
        "draft_status": analysis["draft_status"],
        "pick_count": analysis["pick_count"],
        "adp_source": analysis["adp_source"],
        "warnings": analysis["warnings"],
    }, indent=1))
    return out
