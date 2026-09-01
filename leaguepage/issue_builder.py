"""Issue Builder — assembly layer for weekly and draft/preseason issues.

Editorial sequence, not a CMS: modules can be included/excluded/reordered per
issue, sections carry their own lifecycle, and the handmade feel survives
because nothing forces every issue to have every section.

Content layout for issue_key K (K = "week-NN" or "draft"):
    editorial/<season>/<league>/<K>/
        lowdown/PREP.md            app-generated prep (mentions + evidence)
        lowdown/AUTHORING.md       Claude Code brief (themes/outline/rough)
        lowdown/themes.md          Claude-generated theme proposals
        lowdown/outline.md         Claude-generated outline for chosen theme
        lowdown/rough-lowdown.md   Claude rough draft (ROUGH marker required)
        lowdown/lowdown.md         commissioner-owned final
        sections/<module>.md       generated/edited section prose
        sections/AUTHORING-<module>.md   Claude Code briefs for selections
        AUTHORING_INDEX.md         the week's Claude Code task list

The lowdown stays commissioner-authored: the app preps, Claude drafts rough
material on request, and only Jonathan's edited lowdown.md can publish (it is
credited to him).
"""
from __future__ import annotations

import json
from pathlib import Path

from leaguepage import evidence as ev_mod
from leaguepage.config import EDITORIAL_DIR, League
from leaguepage.matchup_packet import ROUGH_DRAFT_MARKER
from leaguepage.storage import Storage
from leaguepage.team_names import require_public_names, resolve_public_names

WRITING_SKILL = ".claude/skills/my-writing-style/SKILL.md"
BLOCKED_MARKERS = (ROUGH_DRAFT_MARKER, "TEST DRAFT", "provisional label")

# (key, canonical title, leagues, kind)
MODULE_DEFS = [
    ("masthead", "Masthead", ("disco", "surfeit"), "auto"),
    ("lowdown", "The Lowdown", ("disco", "surfeit"), "lowdown"),
    ("hardware", "Weekly Hardware", ("disco", "surfeit"), "section"),
    ("draft-capsules", "Team Draft Capsules", ("disco", "surfeit"), "section"),
    ("ctp", "Common Tactical Picture", ("disco", "surfeit"), "ctp"),
    ("power", "Peer and Near-Peer Competition", ("disco", "surfeit"), "power"),
    ("tracks", "Tracks of Interest", ("disco", "surfeit"), "section"),
    ("fades", "Fades", ("disco", "surfeit"), "section"),
    ("forceflow", "Force Flow", ("disco", "surfeit"), "section"),
    ("blackbox", "Black Box", ("disco", "surfeit"), "section"),
    ("intel", "Intel Prep of the Fantasy Space", ("disco", "surfeit"), "intel"),
    ("branches", "Branches and Sequels", ("surfeit",), "intel"),
    ("false-assumptions", "False Assumptions", ("surfeit",), "section"),
    ("all-city", "The All-City Team", ("disco", "surfeit"), "all-city"),
    ("all-city-marquee", "The All-Marquee Team", ("disco", "surfeit"), "all-city"),
    ("custom", "Custom Section", ("disco", "surfeit"), "section"),
]

WEEKLY_DEFAULT = ["masthead", "lowdown", "hardware", "ctp", "power", "tracks",
                  "fades", "forceflow", "blackbox", "intel", "branches",
                  "false-assumptions", "all-city", "all-city-marquee", "custom"]
DRAFT_DEFAULT = ["masthead", "lowdown", "draft-capsules", "hardware", "power",
                 "false-assumptions", "all-city", "all-city-marquee", "custom"]
# Excluded unless the commissioner includes them. Sidebar features live here:
# they run when there is an edition to run and stay out of the way otherwise.
OPT_IN_MODULES = {"custom", "all-city", "all-city-marquee"}

MIN_INTEL_WEEKS = 5  # before this, playoff math is fake precision — module omits itself


def module_defs_for(league: League, issue_key: str) -> list[tuple[str, str, str]]:
    default = DRAFT_DEFAULT if issue_key == "draft" else WEEKLY_DEFAULT
    out = []
    for key, title, leagues, kind in MODULE_DEFS:
        if league.slug in leagues and key in default:
            out.append((key, title, kind))
    return out


def issue_dir(league: League, season: str, issue_key: str, base_dir: Path | None = None) -> Path:
    return (base_dir or EDITORIAL_DIR) / season / league.slug / issue_key


def _read(path: Path) -> str | None:
    return path.read_text(encoding="utf-8") if path.exists() else None


def _clean(text: str | None) -> bool:
    return bool(text) and not any(b in text for b in BLOCKED_MARKERS)


def lowdown_state(idir: Path) -> tuple[str, str]:
    """(status, detail) for the commissioner-owned Lowdown."""
    final = _read(idir / "lowdown" / "lowdown.md")
    rough = _read(idir / "lowdown" / "rough-lowdown.md")
    prep = _read(idir / "lowdown" / "PREP.md")
    if final and _clean(final):
        return "edited", "commissioner draft present"
    if final:
        return "needs_review", "lowdown.md still carries a blocked marker"
    if rough:
        return "drafting", "rough draft awaiting commissioner edit"
    if prep:
        return "ready", "prep ready; themes/outline/rough via Claude Code"
    return "not_ready", "run the weekly authoring build"


def all_city_state(league: League, season: str, issue_key: str, idir: Path,
                   base_dir: Path | None, module_key: str) -> tuple[str, str]:
    """(status, detail) for a sidebar feature that pairs a validated dataset
    with commissioner prose. The data has to be sound before the copy matters,
    so a broken edition reports needs_review rather than silently rendering.
    The module key IS the feature key, so a rules variant needs no new code."""
    from leaguepage import all_city

    edition = all_city.find_edition(season, issue_key, league.slug,
                                    base_dir=base_dir or EDITORIAL_DIR,
                                    feature_key=module_key)
    if edition is None:
        return "not_ready", (
            f"no {module_key} edition bound to {season}/{issue_key}; add one "
            f"under editorial/features/{module_key}/")
    errors = all_city.validate_edition(edition)
    if errors:
        return "needs_review", f"edition '{edition['edition']}' has {len(errors)} problem(s): {errors[0]}"
    prose = _read(idir / "sections" / f"{module_key}.md")
    if prose is None:
        return "ready", f"edition '{edition['edition']}' validates; section copy not written yet"
    if not _clean(prose):
        return "drafting", "copy present but carries a blocked marker"
    return "edited", f"edition '{edition['edition']}' validates; copy edited"


def module_states(
    storage: Storage,
    league: League,
    season: str,
    issue_key: str,
    *,
    base_dir: Path | None = None,
    week: int | None = None,
) -> list[dict]:
    """Ordered module list with lifecycle state, honoring commissioner
    include/exclude/reorder/approve decisions."""
    idir = issue_dir(league, season, issue_key, base_dir)
    saved = storage.get_issue_modules(league.slug, season, issue_key)
    weeks_played = 0
    matchup_counts = (0, 0)
    if week is not None:
        from leaguepage.matchup_packet import compute_week, matchup_status

        computed = compute_week(storage, league, week)
        if computed:
            weeks_played = computed["analysis"]["weeks_played"]
            total = len(computed["scored"])
            approved = 0
            for sm in computed["scored"]:
                draft = idir / "matchups" / sm["matchup"]["matchup_slug"] / "draft.md"
                if matchup_status(sm["state"], draft.exists()) in ("approved", "locked"):
                    approved += 1
            matchup_counts = (approved, total)

    out = []
    for position, (key, title, kind) in enumerate(module_defs_for(league, issue_key)):
        row = saved.get(key) or {}
        default_included = 0 if (key in OPT_IN_MODULES and not row) else 1
        included = bool(row.get("included", default_included))
        approved = bool(row.get("approved", 0))
        status, detail = "ready", ""
        if kind == "auto":
            status, detail = "ready", "rendered automatically"
            approved = True
        elif kind == "lowdown":
            status, detail = lowdown_state(idir)
            if approved and status == "edited":
                status = "approved"
        elif kind == "ctp":
            a, t = matchup_counts
            if t == 0:
                status, detail = "not_ready", "no matchups"
            elif a < t:
                status, detail = "needs_review", f"{a}/{t} matchup previews approved"
            else:
                status, detail = "approved" if approved else "edited", f"{a}/{t} approved"
        elif kind == "power":
            label = "preseason" if issue_key == "draft" else issue_key
            entries = storage.get_power_rankings(league.slug, season, label)
            if not entries:
                status, detail = "not_ready", f"no '{label}' rankings saved on the Desk"
            else:
                status = "approved" if approved else "edited"
                detail = f"{len(entries)} teams ranked ({label})"
        elif kind == "all-city":
            status, detail = all_city_state(league, season, issue_key, idir, base_dir, key)
            if approved and status == "edited":
                status = "approved"
        elif kind == "intel":
            if weeks_played < MIN_INTEL_WEEKS:
                status, detail = "not_ready", (
                    f"omitted: needs {MIN_INTEL_WEEKS}+ played weeks for meaningful "
                    "playoff leverage (no fake early-season precision)")
                included = False if key not in saved else included
            else:
                status, detail = "ready", "leverage data available; scenario engine is a later phase"
        else:  # section
            text = _read(idir / "sections" / f"{key}.md")
            if text is None:
                status, detail = "not_ready", "no section copy yet"
            elif not _clean(text):
                status, detail = "drafting", "copy present but carries a blocked marker"
            else:
                status = "approved" if approved else "edited"
        saved_pos = row.get("position")
        out.append({
            "module_key": key, "kind": kind,
            "title": row.get("custom_title") or title,
            "position": saved_pos if saved_pos is not None else position,
            "_explicit_pos": saved_pos is not None,
            "_registry_index": position,
            "included": included,
            "approved": approved,
            "status": status,
            "detail": detail,
        })
    # explicit commissioner positions win ties against defaults
    out.sort(key=lambda m: (m["position"], not m["_explicit_pos"], m["_registry_index"]))
    return out


def _module_content_md(storage: Storage, league: League, season: str, issue_key: str,
                       module: dict, idir: Path, public_names: dict[int, str],
                       *, base_dir: Path | None = None) -> str | None:
    key, kind = module["module_key"], module["kind"]
    if kind == "auto":
        return None  # masthead renders in the template
    if kind == "lowdown":
        return _read(idir / "lowdown" / "lowdown.md")
    if kind == "ctp":
        from leaguepage.matchup_packet import compute_week, matchup_status

        week = int(issue_key.removeprefix("week-")) if issue_key.startswith("week-") else None
        if week is None:
            return None
        computed = compute_week(storage, league, week)
        if not computed:
            return None
        parts = []
        for sm in sorted(computed["scored"],
                         key=lambda s: ("FEATURE", "MAJOR", "STANDARD", "CAPSULE").index(
                             (s["state"] or {}).get("prominence_override") or s["recommended_prominence"])):
            m = sm["matchup"]
            draft = _read(idir / "matchups" / m["matchup_slug"] / "draft.md")
            if draft and matchup_status(sm["state"], True) in ("approved", "locked") and _clean(draft):
                names = " vs ".join(public_names[t["roster_id"]] for t in m["teams"])
                body = "\n".join(l for l in draft.splitlines() if not l.strip().startswith("<!--"))
                parts.append(f"### {names}\n\n{body.strip()}")
        return "\n\n".join(parts) if parts else None
    if kind == "power":
        label = "preseason" if issue_key == "draft" else issue_key
        entries = storage.get_power_rankings(league.slug, season, label)
        if not entries:
            return None
        tiers = {1: "Peer Competition", 2: "Near-Peer Competition",
                 3: "Competitive but Flawed", 4: "Strategic Reassessment Required"}
        lines = []
        blurb = _read(idir / "sections" / "power.md")
        if blurb and _clean(blurb):
            lines.append(blurb.strip())
            lines.append("")
        for e in entries:
            name = public_names.get(e["roster_id"], f"Roster {e['roster_id']}")
            tier = f" · {tiers[e['tier']]}" if e.get("tier") in tiers else ""
            note = f" — {e['note']}" if e.get("note") else ""
            lines.append(f"{e['rank']}. **{name}**{tier}{note}")
        return "\n".join(lines)
    if kind == "all-city":
        from leaguepage import all_city

        edition = all_city.find_edition(season, issue_key, league.slug,
                                        base_dir=base_dir or EDITORIAL_DIR,
                                        feature_key=key)
        if edition is None or all_city.validate_edition(edition):
            return None  # unbound or broken edition: assemble_issue warns and blocks
        return all_city.render_section(edition, _read(idir / "sections" / f"{key}.md"))
    if kind == "intel":
        return None  # scenario engine is a later phase; module self-omits
    # Rough/test content is returned so the commissioner preview can show it;
    # assemble() records marker warnings and enforce=True blocks publication.
    text = _read(idir / "sections" / f"{key}.md")
    return text.strip() if text else None


def assemble_issue(
    storage: Storage,
    league: League,
    season: str,
    issue_key: str,
    *,
    base_dir: Path | None = None,
    week: int | None = None,
    enforce: bool = False,
) -> dict:
    """Assembled issue: ordered sections with content, plus warnings. With
    enforce=True, raises ValueError on anything that must block publication."""
    idir = issue_dir(league, season, issue_key, base_dir)
    modules = module_states(storage, league, season, issue_key, base_dir=base_dir, week=week)
    resolved = resolve_public_names(storage, league)
    public_names = {rid: v["name"] for rid, v in resolved.items() if v["name"]}
    warnings: list[str] = []
    for rid, v in resolved.items():
        if v["name"] is None:
            warnings.append(f"Roster {rid} has no confirmed public display name.")
    sections = []
    for module in modules:
        if not module["included"]:
            continue
        content = _module_content_md(storage, league, season, issue_key, module, idir,
                                     public_names, base_dir=base_dir)
        if module["kind"] == "auto":
            sections.append({**module, "content_md": None})
            continue
        if content is None:
            if module["kind"] in ("intel",) or module["module_key"] == "blackbox":
                continue  # sections that legitimately disappear
            warnings.append(f"Included module '{module['title']}' has no publishable content.")
            sections.append({**module, "content_md": None})
            continue
        if not module["approved"]:
            warnings.append(f"Module '{module['title']}' is not approved.")
        for marker in BLOCKED_MARKERS:
            if marker in content:
                warnings.append(f"Module '{module['title']}' contains blocked marker '{marker}'.")
        sections.append({**module, "content_md": content})
    issue_row = storage.get_issue(league.slug, season, issue_key)
    result = {
        "league": league.slug, "season": season, "issue_key": issue_key,
        "theme": (issue_row or {}).get("theme"),
        "sections": sections, "warnings": warnings,
    }
    if enforce and warnings:
        raise ValueError("Issue cannot publish: " + " | ".join(warnings))
    return result


# ------------------------------------------------------------- authoring prep

def _mentions_md(candidates: list[dict], decisions: dict[str, dict]) -> str:
    lines = ["## Things worth mentioning (ranked; commissioner decisions shown)", ""]
    routed = [(c, decisions.get(c["candidate_id"])) for c in candidates]
    routed.sort(key=lambda cd: (cd[1] is None, ))
    for c, d in routed[:10]:
        mark = ""
        if d:
            mark = f" [{d['decision'].upper()}" + (f" → {d['route']}" if d.get("route") else "") + "]"
            if d.get("note"):
                mark += f" note: {d['note']}"
        lines.append(f"- {c['headline']}{mark}")
        for f in c["facts"][:2]:
            lines.append(f"  - {f}")
        lines.append(f"  - evidence: {', '.join(c['evidence'][:4])}")
    return "\n".join(lines)


def build_lowdown_prep(
    storage: Storage,
    league: League,
    season: str,
    issue_key: str,
    candidates: list[dict],
    *,
    base_dir: Path | None = None,
) -> Path:
    idir = issue_dir(league, season, issue_key, base_dir)
    ldir = idir / "lowdown"
    ldir.mkdir(parents=True, exist_ok=True)
    decisions = storage.get_story_decisions(league.slug, season, issue_key)
    issue_row = storage.get_issue(league.slug, season, issue_key) or {}
    theme = issue_row.get("theme")
    prep = "\n".join([
        f"# Lowdown Prep — {league.display_name} {season} {issue_key}",
        "",
        "The Lowdown is commissioner-authored and commissioner-credited. This prep",
        "exists to make starting it dramatically easier; nothing here publishes.",
        "",
        _mentions_md(candidates, decisions),
        "",
        f"Issue theme selected: {theme or '(none)'}",
        "",
    ])
    (ldir / "PREP.md").write_text(prep, encoding="utf-8")
    authoring = f"""# Lowdown AUTHORING — {league.display_name} {season} {issue_key}

**Read `{WRITING_SKILL}` first and follow it.** Jonathan remains the primary
author of The Lowdown; your job is to make starting it easy. Newsletter
register; league theme: {league.subtitle}.

Three deliverables, in order, each on explicit request from the workflow:

1. `themes.md` — three GENUINELY different Lowdown frames (an extended
   historical analogy; a shared Air Force experience; a pop-culture frame; an
   institutional absurdity; a league-wide pattern — pick three distinct
   families, never the same idea titled three ways). For each: title, 2-3
   sentence premise, which PREP.md mentions it would carry, and what the
   payoff turn back to the league looks like.
2. `outline.md` — for the commissioner-selected theme: premise/conclusion
   first, development, specific evidence from PREP.md, the turn back to the
   league, managers/teams touched, payoff, transition into the issue. Use his
   think-in-threes structure where it naturally fits; never force it.
3. `rough-lowdown.md` — ONLY when the workflow explicitly requests the rough
   draft. Full prose, opening with the theme device, paid off per the skill.
   It MUST begin with `<!-- {ROUGH_DRAFT_MARKER} -->` and cannot publish; the
   published Lowdown is Jonathan's edit, saved as `lowdown.md`, credited to him.

Facts come from PREP.md and the issue's packets. No em-dashes; no negated
parallels; run `scripts/style_check.py` on anything you produce.
{f"Issue theme selected by the commissioner: {theme}. Weave it in where it fits; never force every section to participate." if theme else ""}
"""
    (ldir / "AUTHORING.md").write_text(authoring, encoding="utf-8")
    return ldir


def build_section_authoring(
    storage: Storage,
    league: League,
    season: str,
    issue_key: str,
    candidates: list[dict],
    awards: list[dict],
    *,
    base_dir: Path | None = None,
) -> list[Path]:
    """Briefs for every section with commissioner-selected material. Claude
    Code writes sections/<module>.md; commissioner approves on the Desk."""
    idir = issue_dir(league, season, issue_key, base_dir)
    sdir = idir / "sections"
    sdir.mkdir(parents=True, exist_ok=True)
    decisions = storage.get_story_decisions(league.slug, season, issue_key)
    award_decisions = storage.get_award_decisions(league.slug, season, issue_key)
    issue_row = storage.get_issue(league.slug, season, issue_key) or {}
    theme = issue_row.get("theme")
    written = []

    by_section: dict[str, list[tuple[dict, dict]]] = {}
    for c in candidates:
        d = decisions.get(c["candidate_id"])
        if not d or d["decision"] != "include":
            continue
        targets = [d["route"]] if d.get("route") else (c.get("recommended_sections") or ["custom"])
        for t in targets:
            mod = {"force-flow": "forceflow", "black-box": "blackbox",
                   "tracks": "tracks", "fades": "fades", "awards": "hardware",
                   "lowdown": None, "ctp": None, "matchup": None}.get(t, t)
            if mod:
                by_section.setdefault(mod, []).append((c, d))

    def _brief(module_key: str, title: str, body_lines: list[str]) -> Path:
        head = [
            f"# {title} AUTHORING — {league.display_name} {season} {issue_key}",
            "",
            f"**Read `{WRITING_SKILL}` first and follow it.** Newsletter register;",
            f"league theme: {league.subtitle}. Facts come from this brief and its",
            "evidence; if it is not here, it does not exist. Write",
            f"`sections/{module_key}.md`, starting with `<!-- {ROUGH_DRAFT_MARKER} -->`;",
            "the commissioner edits/approves on the Desk before anything publishes.",
            "No em-dashes; no negated parallels; run scripts/style_check.py.",
        ]
        if theme:
            head.append(f"Issue theme: {theme} (weave in only where it genuinely fits).")
        path = sdir / f"AUTHORING-{module_key}.md"
        path.write_text("\n".join(head + [""] + body_lines) + "\n", encoding="utf-8")
        return path

    titles = {key: title for key, title, _, _ in MODULE_DEFS}
    for module_key, items in by_section.items():
        title = titles.get(module_key, module_key)
        lines = [f"## Selected items ({len(items)})", ""]
        for c, d in items:
            lines.append(f"### {c['headline']}")
            for f in c["facts"]:
                lines.append(f"- {f}")
            if d.get("note"):
                lines.append(f"- COMMISSIONER NOTE: {d['note']}")
            lines.append(f"- evidence: {', '.join(c['evidence'])}")
            lines.append("")
        written.append(_brief(module_key, title, lines))

    # hardware brief from decided awards
    decided = []
    for aw in awards:
        d = award_decisions.get(aw["award_key"])
        if d and d["decision"] in ("awarded", "manual"):
            decided.append((aw, d))
    if decided:
        lines = ["## Awards to write (commissioner-decided winners only)", ""]
        for aw, d in decided:
            lines.append(f"### {aw['award_name']} — winner: {d.get('winner') or '(see note)'}")
            lines.append(f"- basis: {aw['metric']}")
            top = next((n for n in aw["nominees"]
                        if (n.get("player") or n.get("team_slug")) == d.get("winner")
                        or n.get("team_slug") == d.get("winner")), None)
            for f in (top or (aw["nominees"][0] if aw["nominees"] else {})).get("facts", []):
                lines.append(f"- {f}")
            if d.get("note"):
                lines.append(f"- COMMISSIONER NOTE: {d['note']}")
            lines.append("")
        lines.append("Target 40-120 words per award; the roast targets the decision or "
                     "outcome, never the person. Do not write copy for undecided awards.")
        written.append(_brief("hardware", "Weekly Hardware", lines))
    return written


def write_authoring_index(league: League, season: str, issue_key: str,
                          *, base_dir: Path | None = None) -> Path:
    idir = issue_dir(league, season, issue_key, base_dir)
    briefs = sorted(p.relative_to(idir).as_posix() for p in idir.rglob("AUTHORING*.md"))
    text = "\n".join([
        f"# Claude Code task list — {league.display_name} {season} {issue_key}",
        "",
        f"Read `{WRITING_SKILL}` once, then work these briefs. Every output is a",
        "rough draft for the commissioner; nothing you write publishes directly.",
        "",
        *[f"- {b}" for b in briefs],
        "",
        "Matchup previews (if any pending) are under matchups/*/generated/AUTHORING.md.",
        "",
    ])
    path = idir / "AUTHORING_INDEX.md"
    path.write_text(text, encoding="utf-8")
    return path
