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
from leaguepage import provenance
from leaguepage.matchup_packet import ROUGH_DRAFT_MARKER
from leaguepage.storage import Storage
from leaguepage.team_names import require_public_names, resolve_public_names

WRITING_SKILL = ".claude/skills/my-writing-style/SKILL.md"
BLOCKED_MARKERS = (ROUGH_DRAFT_MARKER, "TEST DRAFT", "provisional label")

# (key, canonical title, leagues, kind)
#
# Registry, not running order: `WEEKLY_ORDER` below decides where a section
# sits in the paper. Retired entries stay here so an issue that already
# published one can still be assembled and rendered; they are simply no
# longer offered for a new one.
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

# Retired as future authoring concepts. They keep their registry entries and
# their rendering code so a published issue that already contains one still
# assembles and still renders, but they are never offered on a new issue.
#
# Force Flow left for a different reason from the other two: it is now a
# standing league page built from synced transaction data, not something the
# Commissioner rewrites every week. All-City and All-Marquee folded into the
# generic custom-section primitive, which does the same job without two
# bespoke modules to maintain.
RETIRED_MODULES = {"forceflow", "all-city", "all-city-marquee"}

# The paper's running order. The Lowdown opens, the matchups follow, the
# week's special sections come next, the standing sections after them, and
# Weekly Hardware closes every issue. Hardware being last is an invariant,
# not a default: `_order_rank` gives it a rank nothing else can reach.
WEEKLY_ORDER = ["masthead", "lowdown", "ctp", "__custom__", "power", "tracks",
                "fades", "blackbox", "intel", "branches", "false-assumptions",
                "hardware"]
DRAFT_ORDER = ["masthead", "lowdown", "draft-capsules", "__custom__", "power",
               "false-assumptions", "hardware"]

WEEKLY_DEFAULT = [k for k in WEEKLY_ORDER if k != "__custom__"]
DRAFT_DEFAULT = [k for k in DRAFT_ORDER if k != "__custom__"]

# A custom section exists because the Commissioner made one. The first is
# keyed plainly `custom` -- that is the key the single custom section has
# always used, so the prose already on disk needs no migration -- and every
# one after it is `custom-2`, `custom-3`. Nothing here creates one.
CUSTOM_KEY = "custom"
CUSTOM_PREFIX = "custom-"
CUSTOM_DEFAULT_TITLE = "Custom Section"

# Rendered automatically from league theme and issue metadata. It publishes,
# but it is not something he writes, so it stays off the weekly checklist.
NOT_A_WRITING_TASK = {"masthead"}

# Sections whose public body is assembled by code -- the matchup previews for
# Common Tactical Picture, the saved ranking for Peer and Near-Peer -- and
# which also accept an optional commissioner blurb that renders above it.
#
# The blurb is his voice on top of their results. It is optional in the
# strong sense: an absent blurb is not a warning, not a blocker, and not a
# reason the section cannot be approved. Both read `sections/<key>.md`, the
# same file every written section uses, so history, preview, rewrite
# requests and provenance work on them without a second mechanism.
BLURB_MODULES = {"ctp", "power"}

# Nothing starts excluded any more. A section with little to say advises him
# to drop it; it does not drop itself, and it does not make him opt the
# recurring spine back in one section at a time. Custom sections are the one
# thing that has to be asked for, and they are asked for by creating them.
OPT_IN_MODULES: set[str] = set()

# What an included section with no material says, instead of removing
# itself. See `empty` in module_states.
EMPTY_SECTION_NOTE = "No meaningful material this week — consider excluding"

MIN_INTEL_WEEKS = 5  # before this, playoff math is fake precision — module omits itself


def is_custom_key(key: str) -> bool:
    """`custom`, `custom-2`, `custom-3`. The bare key is the first one."""
    return key == CUSTOM_KEY or key.startswith(CUSTOM_PREFIX)


def _custom_index(key: str) -> int:
    if key == CUSTOM_KEY:
        return 1
    tail = key[len(CUSTOM_PREFIX):]
    return int(tail) if tail.isdigit() else 10_000


def next_custom_key(saved: dict) -> str:
    """The next free custom key for this issue.

    Counts from the keys that already exist rather than from how many there
    are, so deleting the middle one of three does not hand the next section
    a key that is already taken.
    """
    used = {k for k in saved if is_custom_key(k)}
    if CUSTOM_KEY not in used:
        return CUSTOM_KEY
    n = 2
    while f"{CUSTOM_PREFIX}{n}" in used:
        n += 1
    return f"{CUSTOM_PREFIX}{n}"


def _order_rank(key: str, order: list[str]) -> tuple[int, int]:
    """Where this section sits in the paper.

    Custom sections share one slot and sort among themselves by creation
    order. Anything the running order does not name sorts just before
    Weekly Hardware, which keeps an unknown or retired-but-still-present
    module inside the issue without ever letting it past the closer.
    """
    if is_custom_key(key):
        return (order.index("__custom__"), _custom_index(key))
    if key == "__never__":                      # unreachable; keeps mypy honest
        return (0, 0)
    if key in order:
        return (order.index(key), 0)
    return (order.index("hardware") - 1, 500)


def module_defs_for(league: League, issue_key: str,
                    saved: dict | None = None) -> list[tuple[str, str, str]]:
    """The sections this issue is made of, in running order.

    `saved` is the issue's stored module rows. It is what makes custom
    sections real: they are not in the static registry, because a custom
    section exists only once the Commissioner has created one, and creating
    one is exactly what writing that row does. A retired module still in
    an issue's saved rows is kept for the same reason -- the issue has one,
    so the issue keeps it -- while never being offered on a new issue.
    """
    saved = saved or {}
    order = DRAFT_ORDER if issue_key == "draft" else WEEKLY_ORDER
    default = DRAFT_DEFAULT if issue_key == "draft" else WEEKLY_DEFAULT
    out = []
    for key, title, leagues, kind in MODULE_DEFS:
        if league.slug not in leagues:
            continue
        if is_custom_key(key):
            continue                      # custom sections come from `saved`
        if key in RETIRED_MODULES:
            # An issue that already carries a retired section keeps it, so
            # that prose still publishes -- Week 1's Force Flow was written
            # before it stopped being a weekly section, and dropping it here
            # would take that writing out of the paper. One he already
            # excluded is simply gone; there is nothing to preserve.
            if (saved.get(key) or {}).get("included"):
                out.append((key, title, kind))
            continue
        if key in default or key in saved:
            out.append((key, title, kind))
    for key in saved:
        if not is_custom_key(key):
            continue
        title = (saved[key] or {}).get("custom_title") or CUSTOM_DEFAULT_TITLE
        out.append((key, title, "section"))
    out.sort(key=lambda d: _order_rank(d[0], order))
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


def matchup_children(
    storage: Storage,
    league: League,
    season: str,
    issue_key: str,
    week: int,
    *,
    base_dir: Path | None = None,
) -> list[dict]:
    """The week's matchup previews, as children of Common Tactical Picture.

    They are not peers of it. A preview has no standing on its own: it is
    one of the pieces CTP is made of, it publishes inside CTP, and CTP is
    finished exactly when they are. Returning them from here rather than
    from a second flat list is what makes that true of readiness and
    approval and not only of the rendered page.

    Ordered the way the section publishes: FEATURE first, CAPSULE last.
    """
    from leaguepage.matchup_interest import PROMINENCE_LEVELS
    from leaguepage.matchup_packet import compute_week, matchup_status

    computed = compute_week(storage, league, week)
    if not computed:
        return []
    idir = issue_dir(league, season, issue_key, base_dir)
    names = resolve_public_names(storage, league)
    out = []
    for sm in computed["scored"]:
        m = sm["matchup"]
        slug = m["matchup_slug"]
        draft = idir / "matchups" / slug / "draft.md"
        prominence = ((sm["state"] or {}).get("prominence_override")
                      or sm["recommended_prominence"])
        status = matchup_status(sm["state"], draft.exists())
        out.append({
            "slug": slug,
            "section": f"matchup:{slug}",
            "anchor": f"sec-matchup-{slug}",
            "title": " vs ".join(
                (names.get(t["roster_id"]) or {}).get("name") or f"Roster {t['roster_id']}"
                for t in m["teams"]),
            "status": status,
            "approved": status in ("approved", "locked"),
            "prominence": prominence,
            "written": draft.exists(),
        })
    out.sort(key=lambda c: (PROMINENCE_LEVELS.index(c["prominence"])
                            if c["prominence"] in PROMINENCE_LEVELS
                            else len(PROMINENCE_LEVELS), c["title"]))
    return out


def ctp_signature(
    storage: Storage,
    league: League,
    season: str,
    issue_key: str,
    week: int | None,
    *,
    base_dir: Path | None = None,
) -> str:
    """A hash over exactly what Common Tactical Picture would publish.

    Approving CTP is approving this writing: the previews that compose
    into it, in order, plus the optional opening remarks. Editing any of
    them changes the signature, and an approval whose signature no longer
    matches is not an approval of what is there now. Nothing has to notice
    the edit or clean up after it — the same reasoning provenance uses.
    """
    from leaguepage import provenance

    if week is None:
        return ""
    idir = issue_dir(league, season, issue_key, base_dir)
    parts = []
    for child in matchup_children(storage, league, season, issue_key, week,
                                  base_dir=base_dir):
        draft = _read(idir / "matchups" / child["slug"] / "draft.md")
        if draft and draft.strip() and _clean(draft):
            parts.append(f"{child['slug']}:{provenance.text_sha(draft)}")
    parts.append("intro:" + provenance.text_sha(_read(idir / "sections" / "ctp.md")))
    return provenance.text_sha("\n".join(parts))


def ctp_approved(
    storage: Storage,
    league: League,
    season: str,
    issue_key: str,
    week: int | None,
    *,
    row: dict | None = None,
    base_dir: Path | None = None,
) -> bool:
    """Whether the section as it stands now is the one he approved."""
    if row is None:
        row = (storage.get_issue_modules(league.slug, season, issue_key) or {}).get("ctp") or {}
    if not row.get("approved"):
        return False
    recorded = row.get("approved_sha")
    # Approved before signatures existed: we know he approved it, we do not
    # know what it said, and un-approving shipped work to say so would be a
    # worse lie than grandfathering it.
    if not recorded:
        return True
    return recorded == ctp_signature(storage, league, season, issue_key, week,
                                     base_dir=base_dir)


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
    children: list[dict] = []
    if week is not None:
        from leaguepage.matchup_packet import compute_week

        computed = compute_week(storage, league, week)
        if computed:
            weeks_played = computed["analysis"]["weeks_played"]
        children = matchup_children(storage, league, season, issue_key, week,
                                    base_dir=base_dir)

    out = []
    for position, (key, title, kind) in enumerate(module_defs_for(league, issue_key, saved)):
        row = saved.get(key) or {}
        kids = children if kind == "ctp" else []
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
            # The parent is exactly its children, so one approval covers
            # the writing they publish. It goes stale on its own the moment
            # any preview or the opening changes, because the approval
            # carries a signature over that exact text.
            t = len(kids)
            written = sum(1 for c in kids if c.get("written"))
            approved = ctp_approved(storage, league, season, issue_key, week,
                                    row=row, base_dir=base_dir)
            if t == 0:
                status, detail = "not_ready", "no matchups"
            elif written < t:
                status, detail = ("edited",
                                  f"{written} / {t} previews written")
            elif approved:
                status, detail = "approved", f"{t} previews, approved together"
            else:
                status, detail = "edited", f"{t} previews written; approve the section"
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
                    f"needs {MIN_INTEL_WEEKS}+ played weeks for meaningful playoff "
                    "leverage (no fake early-season precision)")
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
            "custom": is_custom_key(key),
            # Masthead publishes but is not something he writes, so it is
            # not one of the week's tasks and does not count toward being
            # finished.
            # On the weekly checklist, and counted toward being finished.
            # Masthead publishes without being written; a retired section
            # that survives inside an old issue still publishes but is no
            # longer one of the week's jobs.
            "checklist": key not in NOT_A_WRITING_TASK and key not in RETIRED_MODULES,
            "retired": key in RETIRED_MODULES,
            "children": kids,
            "children_approved": sum(1 for c in kids if c["approved"]),
            "children_written": sum(1 for c in kids if c.get("written")),
            "children_total": len(kids),
            "title": row.get("custom_title") or title,
            "position": saved_pos if saved_pos is not None else position,
            "_explicit_pos": saved_pos is not None,
            "_registry_index": position,
            "included": included,
            "approved": approved,
            "status": status,
            "detail": detail,
            # An included section with nothing in it is the Commissioner's
            # call to make, not the system's. Dropping it for him hides the
            # decision; leaving it silent makes him work out for himself
            # why the issue will not assemble. Say it instead.
            "empty": bool(included and kind not in ("auto", "ctp")
                          and status == "not_ready"),
        })
    # Running order comes from the registry, and Weekly Hardware closes the
    # issue. A saved `position` used to win outright, which meant a row
    # written before Hardware was pinned last could still float it back into
    # the middle of the paper. Explicit positions now order custom sections
    # against each other -- the one place the Commissioner sets them -- and
    # nothing else.
    order = DRAFT_ORDER if issue_key == "draft" else WEEKLY_ORDER

    def sort_key(m):
        slot, within = _order_rank(m["module_key"], order)
        # A custom section he has positioned sorts by that position; the
        # creation-order index is the fallback. Coerced, because `position`
        # is a nullable TEXT-tolerant column and comparing a string against
        # an int raises rather than sorting.
        if is_custom_key(m["module_key"]) and m["_explicit_pos"]:
            try:
                within = int(m["position"])
            except (TypeError, ValueError):
                pass
        return (slot, within, m["_registry_index"])

    out.sort(key=sort_key)
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
        from leaguepage.matchup_interest import PROMINENCE_LEVELS
        from leaguepage.matchup_packet import compute_week, matchup_status

        week = int(issue_key.removeprefix("week-")) if issue_key.startswith("week-") else None
        if week is None:
            return None
        computed = compute_week(storage, league, week)
        if not computed:
            return None
        parts = []
        for sm in sorted(computed["scored"],
                         key=lambda s: PROMINENCE_LEVELS.index(
                             (s["state"] or {}).get("prominence_override") or s["recommended_prominence"])):
            m = sm["matchup"]
            draft = _read(idir / "matchups" / m["matchup_slug"] / "draft.md")
            # Every written preview belongs to the section. Individual
            # sign-off used to decide membership, which meant an unapproved
            # preview vanished from the page silently; the published unit is
            # Common Tactical Picture, and one approval covers it.
            if draft and draft.strip() and _clean(draft):
                names = " vs ".join(public_names[t["roster_id"]] for t in m["teams"])
                body = "\n".join(l for l in draft.splitlines() if not l.strip().startswith("<!--"))
                # Each preview carries its own provenance line under its
                # heading; the parent section carries none, so one badge
                # never describes six different pieces of writing.
                line = provenance.inline_html(provenance.state_for(
                    storage, league_slug=league.slug, season=season, issue_key=issue_key,
                    section=f"matchup:{m['matchup_slug']}", text=draft))
                parts.append(f"### {names}\n\n" + (f"{line}\n\n" if line else "")
                             + body.strip())
        if not parts:
            return None
        # His optional lead-in, above the previews. Only when there is
        # something for it to lead into: the parent is exactly its children,
        # so a blurb standing alone is not a Common Tactical Picture.
        blurb = _read(idir / "sections" / "ctp.md")
        if blurb and _clean(blurb):
            line = provenance.inline_html(provenance.state_for(
                storage, league_slug=league.slug, season=season, issue_key=issue_key,
                section="ctp", text=blurb))
            parts.insert(0, (f"{line}\n\n" if line else "") + blurb.strip())
        return "\n\n".join(parts)
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
    # Same warnings, each with the key of the module it is about. The Desk
    # used to match a warning back to its section by TITLE, and titles are
    # free text on a custom section: two sections called the same thing
    # collided, and a custom section named after a standing one handed him
    # a button that excluded the standing one instead.
    warning_rows: list[dict] = []

    def warn(text: str, module_key: str | None = None, kind: str = "generic") -> None:
        warnings.append(text)
        warning_rows.append({"text": text, "module_key": module_key, "kind": kind})

    for rid, v in resolved.items():
        if v["name"] is None:
            warn(f"Roster {rid} has no confirmed public display name.")
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
            warn(f"Included module '{module['title']}' has no publishable content.",
                 module["module_key"], "empty-section")
            sections.append({**module, "content_md": None})
            continue
        if not module["approved"]:
            warn(f"Module '{module['title']}' is not approved.",
                 module["module_key"], "unapproved")
        for marker in BLOCKED_MARKERS:
            if marker in content:
                warn(f"Module '{module['title']}' contains blocked marker '{marker}'.",
                     module["module_key"], "blocked-marker")
        sections.append({**module, "content_md": content})
    # An issue with nothing in it produced no warnings at all, so every
    # gate passed and the Desk said READY TO PUBLISH. Publishing it froze a
    # blank page into the immutable record, and the republish guard then
    # refused to correct it in place. Excluding sections one at a time to
    # see what a thin week looks like is an ordinary thing to do.
    if not any(s.get("content_md") for s in sections):
        warn("This issue has no publishable content: every section is "
             "excluded, empty, or automatic.", None, "empty-issue")
    issue_row = storage.get_issue(league.slug, season, issue_key)
    result = {
        "league": league.slug, "season": season, "issue_key": issue_key,
        "theme": (issue_row or {}).get("theme"),
        "sections": sections, "warnings": warnings,
        "warning_rows": warning_rows,
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
    extra: list[str] | None = None,
) -> Path:
    """`extra` is the Command Brief's top stories, when the caller has
    them: the strongest material goes first, ahead of the raw candidate
    list, because that is the order he should read it in."""
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
        *((extra + [""]) if extra else []),
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
            # "lowdown" mapped to None, so a story he deliberately routed to
            # the Lowdown produced no brief at all. The Lowdown has its own
            # PREP file; sending a routed story there as well means the
            # decision is visible in exactly one place either way.
            mod = {"force-flow": "forceflow", "black-box": "blackbox",
                   "tracks": "tracks", "fades": "fades", "awards": "hardware",
                   "lowdown": "lowdown", "ctp": None, "matchup": None}.get(t, t)
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
