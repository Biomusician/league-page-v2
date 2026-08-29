"""Weekly matchup packets — one dossier directory per matchup.

Layout:

    editorial/<season>/<league>/week-<NN>/
        generated/week.json                     queue summary (regenerated)
        matchups/<slug>/
            commissioner_notes.md               Jonathan's, created once, never overwritten
            draft.md                            Claude Code writes; Jonathan edits
            generated/                          regenerated every build
                data.json  analytics.json  history.md  story_memory.md
                angles.md  evidence.json  AUTHORING.md

AUTHORING.md requires reading the authoritative my-writing-style skill before
writing and separates FACT / COMPUTED METRIC / CONFIRMED EDITORIAL LORE /
ARCHIVE CALLBACK / COMMISSIONER NOTE / EDITORIAL INFERENCE / UNVERIFIED.
"""
from __future__ import annotations

import json
from pathlib import Path

from leaguepage.adp import load_adp_for_league
from leaguepage.config import EDITORIAL_DIR, League
from leaguepage.draft_analysis import analyze_league_draft
from leaguepage.editorial import load_coalitions, load_managers
from leaguepage.matchup_analysis import analyze_week
from leaguepage.matchup_angles import generate_angles
from leaguepage.matchup_interest import (
    WORD_TARGETS, classify, competitive_importance, recommend_prominence, story_value,
)
from leaguepage.storage import Storage, utcnow_iso
from leaguepage.story_memory import story_memory_for_matchup

WRITING_SKILL = ".claude/skills/my-writing-style/SKILL.md"
ROUGH_DRAFT_MARKER = "ROUGH DRAFT - COMMISSIONER EDIT REQUIRED"


def week_dir(league: League, season: str, week: int, base_dir: Path | None = None) -> Path:
    return (base_dir or EDITORIAL_DIR) / season / league.slug / f"week-{week:02d}"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _history_md(matchup: dict) -> str:
    lines = [f"# History — {matchup['matchup_slug']}", ""]
    h2h = matchup.get("h2h") or {}
    if h2h.get("meetings"):
        lines.append(f"- H2H record (this league's synced history): {h2h['record']}")
        for m in h2h["meetings"]:
            lines.append(f"- Week {m['week']}: {m['points']} (winner roster {m['winner']})")
        last = h2h.get("last_meeting")
        if last:
            lines.append(f"- Last meeting: week {last['week']}, {last['points']}")
    else:
        lines.append("- No prior meetings in the synced league history. Do not invent any.")
    lines.append("")
    return "\n".join(lines)


def _story_memory_md(sm: dict) -> str:
    lines = ["# Story Memory — the allowed editorial-memory set", "",
             f"Scoping rule: {sm['scoping_rule']}", ""]
    lines.append("## Archive callbacks (ranked; prefer the one strong callback)")
    if sm["callbacks"]:
        for c in sm["callbacks"]:
            flags = []
            if c["cross_league"]:
                flags.append("CROSS-LEAGUE (explicitly approved)")
            if c["date_unreliable"]:
                flags.append("dating not high-confidence: no date-specific claims")
            if c["prior_reuse"]:
                flags.append(f"already used {c['prior_reuse']}x this season")
            lines.append(f"- [{c['strength']}] '{c['matched_term']}' in {c['title']} "
                         f"({c['source_league']} {c['season'] or '????'}"
                         + (f" wk{c['week']}" if c["week"] else "") + f", {c['evidence']})"
                         + (f" [{'; '.join(flags)}]" if flags else ""))
            lines.append(f"  - snippet: {c['snippet']}")
    else:
        lines.append("- None. This matchup gets no archive callbacks; do not fabricate history.")
    lines.append("")
    lines.append("## Tracked takes involving these teams")
    if sm["takes"]:
        for t in sm["takes"]:
            lines.append(f"- take:{t['take_id']} [{t['status']}] \"{t['quote']}\" (subject {t['subject']})")
    else:
        lines.append("- None.")
    lines.append("")
    lines.append("## Award history")
    for a in sm["awards"] or []:
        lines.append(f"- {a['workflow']}: {a['award_key']} -> {a['winner']}")
    if not sm["awards"]:
        lines.append("- None.")
    lines.append("")
    lines.append("## Recurring bits (confirmed manager metadata)")
    for b in sm["recurring_bits"] or []:
        sens = f" [{b['sensitivity']}]" if b["sensitivity"] != "fair_game" else ""
        lines.append(f"- {b['team_slug']}: {b['bit']}{sens} ({b['evidence']})")
    if not sm["recurring_bits"]:
        lines.append("- None recorded.")
    lines.append("")
    return "\n".join(lines)


def _angles_md(angles: list[dict], state: dict | None) -> str:
    selected = (state or {}).get("selected_angle_id")
    custom = (state or {}).get("custom_angle")
    lines = ["# Story angles (rule-generated premises; commissioner decides)", ""]
    if custom:
        lines += ["## COMMISSIONER CUSTOM ANGLE (controls)", f"> {custom}", ""]
    for a in angles:
        mark = " ← SELECTED" if a["angle_id"] == selected else ""
        lines.append(f"## [{a['strength']}] {a['title']}{mark}")
        lines.append(f"- angle_id: `{a['angle_id']}` · family: {a['family']}")
        lines.append(f"- premise: {a['premise']}")
        lines.append(f"- why it fits: {a['why']}")
        if a.get("callback"):
            lines.append(f"- optional callback: {a['callback']['title']} ({a['callback']['evidence']})")
        for w in a["collision_warnings"]:
            lines.append(f"- RECENTLY USED: {w}")
        lines.append(f"- evidence: {', '.join('`' + e + '`' for e in a['evidence'])}")
        lines.append("")
    return "\n".join(lines)


def _authoring_md(league: League, matchup: dict, scored: dict, state: dict | None,
                  angles: list[dict], notes_text: str) -> str:
    prominence = (state or {}).get("prominence_override") or scored["recommended_prominence"]
    words = WORD_TARGETS[prominence]
    selected_id = (state or {}).get("selected_angle_id")
    custom = (state or {}).get("custom_angle")
    selected = next((a for a in angles if a["angle_id"] == selected_id), None)
    if custom:
        angle_txt = f"COMMISSIONER CUSTOM ANGLE (controls):\n> {custom}"
    elif selected:
        angle_txt = (f"SELECTED: {selected['title']} ({selected['angle_id']})\n"
                     f"> {selected['premise']}")
    else:
        angle_txt = ("No angle selected yet. Either wait for the commissioner, or draft "
                     "from the strongest non-colliding angle in angles.md and say which "
                     "you used at the top of the draft.")
    revisions = (state or {}).get("revision_requests")
    if isinstance(revisions, str):
        try:
            revisions = json.loads(revisions)
        except Exception:
            revisions = [revisions]
    rev_txt = "\n".join(f"- {r}" for r in (revisions or [])) or "- none"
    tags = ", ".join(scored["tags"]) or "none"
    return f"""# AUTHORING — {matchup['matchup_slug']} ({league.display_name} {matchup['season']} week {matchup['week']})

**Before writing a word: read `{WRITING_SKILL}` (the authoritative
`my-writing-style` skill) and follow it.** This workflow is Jonathan's
explicit drafting request, so write the full preview (not an outline), aiming
for 80% finished. Newsletter register. League theme: {league.subtitle}.

## Assignment

- Prominence: **{prominence}** — target {words} words.
- Editorial tags: {tags}
- {angle_txt}
- Revision requests from the commissioner (address all):
{rev_txt}

## Commissioner notes (verbatim)

{notes_text.strip() or '(none)'}

## Provenance classes in this packet — what each may support

- **FACT** (`data.json`, `history.md`): state freely, phrased with its source.
- **COMPUTED METRIC** (`analytics.json` components): state with its basis; the
  interest scores themselves are editorial heuristics, never "objective".
- **CONFIRMED EDITORIAL LORE** (`story_memory.md` recurring bits, coalition
  facts): usable as fact.
- **ARCHIVE CALLBACK** (`story_memory.md` callbacks): quote/attribute to the
  source issue; respect date-reliability and cross-league flags.
- **COMMISSIONER NOTE**: his voice; may be stated as his position.
- **EDITORIAL INFERENCE** (angles, premises): suggestions, never facts.
- **UNVERIFIED / DO NOT ASSERT**: anything not in this packet, plus everything
  the manager-context BANNED lists name. If it is not here, it does not exist.

## Rules

- Facts come from this packet; you supply the angle, structure, and prose.
- `unavailable` in data.json lists what the data cannot support (projections,
  preseason records). Absence is stated or worked around, never papered over.
- No fabricated smack talk, no invented motivation, no faux broadcaster copy.
- A prediction only if this issue's workflow calls for one.
- Sweep per the skill for em-dashes and the entire negated-parallel family,
  then run: `.venv/Scripts/python.exe scripts/style_check.py <draft>`.

## Deliverable

Write `../draft.md`, starting with:

    <!-- {ROUGH_DRAFT_MARKER} -->

then the preview at its target length. Log the angle/frame/callback you used
at the end of the file as an HTML comment for the usage tracker, e.g.:

    <!-- usage: angle={selected_id or 'chosen-angle-id'} frame=<family> callback=<evidence-ref-or-none> joke_family=<lane-or-none> -->
"""


def compute_week(storage: Storage, league: League, week: int) -> dict | None:
    """Everything the Desk and the packet writer need, computed in memory:
    {analysis, scored: [{matchup, story_memory, competitive_importance,
    story_value, tags, angles, state, recommended_prominence}]}."""
    managers = load_managers()
    coalitions = load_coalitions()
    analysis = analyze_week(storage, league, week, managers=managers)
    if analysis is None:
        return None
    season = analysis["season"]

    # draft-derived construction context (optional, used for contrast angles)
    draft_context: dict = {}
    draft_analysis = analyze_league_draft(storage, league, managers=managers,
                                          adp=load_adp_for_league(league))
    if draft_analysis:
        draft_context = {t["team_slug"]: {"early_rounds_positions": t["early_rounds_positions"]}
                         for t in draft_analysis["teams"]}

    states = storage.list_matchup_states(league.slug, season, week)
    scored_matchups = []
    for matchup in analysis["matchups"]:
        state = states.get(matchup["matchup_slug"])
        sm = story_memory_for_matchup(storage, league.slug, season, matchup, managers)
        ci = competitive_importance(matchup, analysis)
        sv = story_value(matchup, coalitions=coalitions, story_memory=sm,
                         draft_context=draft_context,
                         commissioner_flagged=bool((state or {}).get("angle_note")))
        tags = classify(matchup, ci, sv, analysis)
        angles = generate_angles(storage, matchup, sm, coalitions=coalitions,
                                 draft_context=draft_context)
        scored_matchups.append({
            "matchup": matchup, "story_memory": sm, "competitive_importance": ci,
            "story_value": sv, "tags": tags, "angles": angles, "state": state,
        })
    recommend_prominence(scored_matchups)
    return {"analysis": analysis, "scored": scored_matchups}


def matchup_status(state: dict | None, draft_exists: bool) -> str:
    status = (state or {}).get("status") or (
        "ready_to_draft" if (state or {}).get("selected_angle_id") or (state or {}).get("custom_angle")
        else "packet_ready")
    if draft_exists and status in ("packet_ready", "ready_to_draft", "angle_needed"):
        status = "drafted"
    return status


def build_weekly_packet(
    storage: Storage,
    league: League,
    week: int,
    *,
    base_dir: Path | None = None,
) -> Path | None:
    computed = compute_week(storage, league, week)
    if computed is None:
        return None
    analysis = computed["analysis"]
    scored_matchups = computed["scored"]
    season = analysis["season"]
    root = week_dir(league, season, week, base_dir)

    queue = []
    for s in scored_matchups:
        matchup, state = s["matchup"], s["state"]
        slug = matchup["matchup_slug"]
        mdir = root / "matchups" / slug
        gen = mdir / "generated"

        notes_path = mdir / "commissioner_notes.md"
        if not notes_path.exists():
            _write(notes_path, f"# Commissioner notes — {slug}\n\n(none yet)\n")
        notes_text = notes_path.read_text(encoding="utf-8")

        _write(gen / "data.json", json.dumps(s["matchup"], indent=1, ensure_ascii=False))
        _write(gen / "analytics.json", json.dumps({
            "competitive_importance": s["competitive_importance"],
            "story_value": s["story_value"],
            "tags": s["tags"],
            "recommended_prominence": s["recommended_prominence"],
            "prominence_override": (state or {}).get("prominence_override"),
            "score_disclaimer": "Interest scores are editorial heuristics with visible "
                                "components; they are not objective measurements.",
        }, indent=1, ensure_ascii=False))
        _write(gen / "history.md", _history_md(matchup))
        _write(gen / "story_memory.md", _story_memory_md(s["story_memory"]))
        _write(gen / "angles.md", _angles_md(s["angles"], state))
        all_refs = sorted({
            e for e in matchup["evidence"]
        } | {e for a in s["angles"] for e in a["evidence"]}
          | {c["evidence"] for c in s["story_memory"]["callbacks"]}
          | {f"take:{t['take_id']}" for t in s["story_memory"]["takes"]})
        _write(gen / "evidence.json", json.dumps({"references": all_refs}, indent=1))
        _write(gen / "AUTHORING.md",
               _authoring_md(league, matchup, s, state, s["angles"], notes_text))

        draft_path = mdir / "draft.md"
        status = matchup_status(state, draft_path.exists())
        queue.append({
            "matchup_slug": slug,
            "teams": [t["team_slug"] for t in matchup["teams"]],
            "recommended_prominence": s["recommended_prominence"],
            "prominence_override": (state or {}).get("prominence_override"),
            "competitive_importance": s["competitive_importance"]["score"],
            "story_value": s["story_value"]["score"],
            "tags": s["tags"],
            "selected_angle_id": (state or {}).get("selected_angle_id"),
            "custom_angle": bool((state or {}).get("custom_angle")),
            "status": status,
            "has_draft": draft_path.exists(),
        })

    _write(root / "generated" / "week.json", json.dumps({
        "generated_at": utcnow_iso(),
        "league": league.slug, "season": season, "week": week,
        "weeks_played": analysis["weeks_played"],
        "queue": queue,
    }, indent=1, ensure_ascii=False))
    return root
