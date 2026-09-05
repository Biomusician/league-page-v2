"""Static public site builder — the deployable artifact.

Renders dist/ (or any out_dir) from local data at build time: no server, no
API keys, no page-view Sleeper calls. Published issues render ONLY from the
frozen snapshots in published/, so rebuilds never mutate an already-published
issue. The Commissioner's Desk, editorial packets, evidence, notes, and the
local DB never enter the output; audit_output() verifies that.

URL scheme (clean, bookmarkable, static-host friendly):
    /                      index.html            league selector
    /{league}/             .../index.html        front page (latest issue)
    /{league}/matchups/    Common Tactical Picture (current week)
    /{league}/standings/ /power/ /teams/ /transactions/ /draft/ /black-box/ /archive/
    /{league}/team/{slug}/
    /{league}/archive/a{id}/                     imported historical issue
    /{league}/{season}/{issue_key}/              issue permalinks
"""
from __future__ import annotations

import json
import re
import shutil
from collections import defaultdict
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from leaguepage.config import (DIST_DIR, LEAGUES, PUBLISHED_DIR, SITE_URL, STATIC_DIR,
                               SUPPORT_LABEL, SUPPORT_URL,
                               TEMPLATES_DIR, League)
from leaguepage.privacy import (ALWAYS_FORBIDDEN, MIN_HANDLE_LEN, PRIVATE_PATTERNS,
                                handle_re, published_matcher)
from leaguepage import prose, provenance
from leaguepage.draft_value import SKILL_POSITIONS
from leaguepage.editorial import load_coalitions
from leaguepage.matchup_analysis import (all_play, analyze_week, season_efficiency,
                                         team_record, weekly_scores)
from leaguepage.matchup_packet import compute_week, matchup_status, week_dir
from leaguepage.publish import BLOCKED_MARKERS, strip_editorial_comments
from leaguepage.storage import Storage
from leaguepage.team_analytics import lineup_slots
from leaguepage.team_names import resolve_public_names

FLAGS = {"France": "\U0001F1EB\U0001F1F7", "United Kingdom": "\U0001F1EC\U0001F1E7",
         "Japan": "\U0001F1EF\U0001F1F5", "Sweden": "\U0001F1F8\U0001F1EA"}

def _issue_description(ctx: dict, league) -> str:
    """A shared issue link should show its own headline, not the league name."""
    head = (ctx.get("headline") or "").strip()
    label = ctx.get("issue_label") or "Issue"
    if head:
        return f"{head} \u2014 {label}, {league.display_name}."
    return f"{label} of {league.display_name}."


def _home_description(league, front: dict | None, latest: dict | None,
                      weeks_played: int, week: int) -> str:
    """The front page's own lead is the hook. Falling back to a generic line
    is fine; inventing one is not."""
    lead = ((front or {}).get("lead") or {})
    hook = (lead.get("headline") or "").strip()
    if hook:
        return f"{hook} \u2014 {league.display_name}, {_through(weeks_played)}."
    head = ((latest or {}).get("headline") or "").strip()
    if head:
        return f"{head} \u2014 {league.display_name}."
    return (f"{league.display_name}: the week read through synced data, "
            f"{_through(weeks_played)}.")


def _draft_description(league, analysis: dict | None) -> str:
    picks = len((analysis or {}).get("picks") or [])
    if not picks:
        return f"The {league.display_name} draft board."
    return (f"All {picks} {league.display_name} picks against the consensus "
            f"board, with every reach and every value on the record.")


def _own_stake(lv: dict | None, names: dict[int, str], next_card) -> dict | None:
    """What this week's own game is worth to this team, or nothing.

    A manager who is 90% in whatever happens has no stake on Sunday, and
    saying so is more useful than printing a number that will not move.
    """
    if not lv or not lv["material"]:
        return None
    from leaguepage.leverage import describe_stake
    from leaguepage.team_analytics import format_odds

    return {"if_win": format_odds(lv["if_win"]), "if_lose": format_odds(lv["if_lose"]),
            "opponent": names.get(lv["opponent"], ""),
            "verdict": describe_stake(lv["if_win"], lv["if_lose"]),
            "anchor": (next_card or {}).get("anchor")}


def _archive_label(item: dict) -> str:
    """What to call an archived issue on a listing.

    The indexed season and week, because they are what the file declares and
    what the listing is sorted and grouped by. A title that says a different
    year is preserved next to this, never in place of it.
    """
    season, week = item.get("season"), item.get("week")
    if season and week:
        return f"{season} · Week {week}"
    if season:
        return f"{season} · {item.get('title') or 'issue'}"
    return item.get("title") or "archive issue"


def _archive_depth(storage: Storage, league: League) -> dict | None:
    """How much league history is on file, and a door into it.

    The Black Box was six rows restating pages a reader had already seen,
    with no link on any of them. The corpus behind it is the most
    distinctive thing this site has.
    """
    scope = {"disco": ("disco", "daddy"), "surfeit": ()}.get(league.slug, ())
    items = [i for key in scope for i in storage.list_archive_issues(key)]
    if not items:
        return None
    seasons = sorted({i["season"] for i in items if i.get("season")})
    return {"issues": len(items),
            "first": seasons[0] if seasons else None,
            "last": seasons[-1] if seasons else None}


def _through(weeks_played: int) -> str:
    """'through week 6' / 'before a game has been played'."""
    if not weeks_played:
        return "before a game has been played"
    return f"through week {weeks_played}"


# The mark a shared link shows. Both are already in the deployed assets and
# both clear the 300x157 floor a preview card needs.
OG_IMAGES = {
    "disco": ("disco-logo-light.png", 800, 300),
    "surfeit": ("surfeit-badge.png", 560, 528),
}

AWARD_NAMES = {
    "shame": "Shame! Shame! Shame!", "manager-of-the-week": "Manager of the Week",
    "galaxy-brain": "Galaxy Brain", "hard-luck-bastard": "Hard-Luck Bastard",
    "waiver-wire-heist": "Waiver Wire Heist", "faab-arsonist": "FAAB Arsonist",
    "upset-of-the-week": "Upset of the Week", "mercy-rule": "Mercy Rule",
    "escape-artist": "Escape Artist", "benchwarmer-memorial": "Benchwarmer Memorial",
    "biggest-reach": "Biggest Reach", "best-value": "Best Value",
    "draft-crusher": "Draft Crusher",
    "most-aggressive-construction": "Most Aggressive Construction",
    "most-interesting-strategy": "Most Interesting Strategy",
    "most-likely-to-age-badly": "Most Likely to Age Badly",
}


def _render_md(text: str) -> str:
    return prose.render(strip_editorial_comments(text))


def _env() -> Environment:
    return Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=True)


def _write(out_dir: Path, rel: str, html: str, pages: list[str]) -> None:
    path = out_dir / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    pages.append(rel)


def _load_snapshots(league: League, published_dir: Path) -> list[dict]:
    """One entry per issue, rendering the LATEST revision of it.

    Corrections are additive files (`draft.r2.json` beside `draft.json`), so
    an issue is a family: the original is the record of what shipped that
    day, the newest revision is what readers see, and the correction history
    travels with it for the 'Updated …' line."""
    root = published_dir / league.slug
    families: dict[tuple[str, str], list[dict]] = {}
    if root.exists():
        for p in sorted(root.rglob("*.json")):
            snap = json.loads(p.read_text(encoding="utf-8"))
            key = (snap["season"], snap.get("revises") or snap["issue_key"])
            families.setdefault(key, []).append(snap)
    snaps = []
    for (season, issue_key), family in families.items():
        family.sort(key=lambda s: int(s.get("revision") or 1))
        snap = dict(family[-1])
        snap["issue_key"] = issue_key
        snap["href"] = f"{season}/{issue_key}/index.html"
        snap["revisions"] = [
            {"revision": int(s.get("revision") or 1),
             "at": (s.get("revised_at") or s.get("published_at") or "")[:10],
             "note": s.get("revision_note")}
            for s in family[1:]
        ]
        snaps.append(snap)
    # newest first: season desc, weekly issues over draft, week number desc
    def _key(s):
        wk = s["issue_key"]
        return (s["season"], 1 if wk.startswith("week-") else 0, wk)
    snaps.sort(key=_key, reverse=True)
    return snaps


def _strip_duplicate_heading(content_md: str, title: str) -> str:
    """Drop a leading markdown heading that just repeats the module title."""
    lines = content_md.lstrip().splitlines()
    if lines and re.match(rf"#+\s*{re.escape(title)}\s*$", lines[0], re.I):
        return "\n".join(lines[1:]).lstrip()
    return content_md


def _issue_ctx(snap: dict, *, preview: bool = False) -> dict:
    sections = []
    for s in snap["sections"]:
        sections.append({
            "anchor": s["module_key"],
            "title": s["title"],
            "credit": s.get("credit"),
            # Absent on every snapshot frozen before provenance existed,
            # which is the right answer for them: nothing was recorded, so
            # nothing is claimed.
            "provenance": s.get("provenance"),
            "html": _render_md(_strip_duplicate_heading(s["content_md"], s["title"])),
        })
    lowdown = next((s for s in snap["sections"] if s["module_key"] == "lowdown"), None)
    excerpt = headline = None
    if lowdown:
        parts = lowdown["content_md"].split("\n\n")
        # The Lowdown's own headline is the first heading that is not just the
        # module's name again ("# The Lowdown" / "## Vol 8.I: Back On
        # Station"). It is the single best thing to put at the top of the
        # front page, so lift it out rather than making a reader click
        # through to find out what the issue is about.
        for p in parts:
            p = p.strip()
            if p.startswith("#"):
                text = p.lstrip("#").strip()
                if text.lower() != (snap.get("issue_label") or "").lower() \
                        and text.lower() != lowdown["title"].lower():
                    headline = text
                    break
        paras = [p for p in parts if p.strip() and not p.strip().startswith("#")]
        excerpt = _render_md("\n\n".join(paras[:2]))
    return {
        "issue_key": snap["issue_key"], "issue_label": snap["issue_label"],
        "season": snap["season"], "published_at": snap.get("published_at"),
        "preview": preview, "sections": sections,
        "section_titles": [{"anchor": s["anchor"], "title": s["title"]} for s in sections],
        "lowdown_excerpt_html": excerpt,
        "headline": headline,
        "href": snap["href"],
        "revisions": snap.get("revisions") or [],
        "revised_at": (snap.get("revised_at") or "")[:10] or None,
        "revision_note": snap.get("revision_note"),
    }


MAX_SCAN_WEEK = 18  # scan the whole stored season regardless of current_week


def _standings_rows(storage: Storage, league: League, names: dict[int, dict],
                    week: int) -> tuple[list[dict], int]:
    rosters = storage.get_rosters(league.league_id)
    scores = weekly_scores(storage, league.league_id, MAX_SCAN_WEEK)
    ap = all_play(scores)
    weeks_played = len({wk for rows in scores.values() for wk, _ in rows})
    analysis = analyze_week(storage, league, week) if weeks_played else None
    a_teams = {t["roster_id"]: t for t in (analysis or {}).get("teams", {}).values()} if analysis else {}
    pa: dict[int, float] = defaultdict(float)
    for wk in range(1, MAX_SCAN_WEEK + 1):
        rows = [r for r in storage.get_matchups(league.league_id, wk) if r.get("matchup_id") is not None]
        by_mid: dict[int, list[dict]] = defaultdict(list)
        for r in rows:
            by_mid[r["matchup_id"]].append(r)
        for pair in by_mid.values():
            if len(pair) == 2 and any((p.get("points") or 0) > 0 for p in pair):
                pa[pair[0]["roster_id"]] += float(pair[1].get("points") or 0)
                pa[pair[1]["roster_id"]] += float(pair[0].get("points") or 0)
    fpts_rank = {r["roster_id"]: i + 1 for i, r in enumerate(
        sorted(rosters, key=lambda r: -team_record(r)["fpts"]))}
    slots = lineup_slots(storage.get_league(league.league_id) or {})
    season_eff = (season_efficiency(storage, slots, league.league_id, MAX_SCAN_WEEK)
                  if weeks_played else {})
    rows_out = []
    for r in sorted(rosters, key=lambda r: (-(r.get("settings") or {}).get("wins", 0),
                                            -team_record(r)["fpts"])):
        rid = r["roster_id"]
        rec = team_record(r)
        apd = ap.get(rid)
        at = a_teams.get(rid, {})
        eff = season_eff.get(rid)
        rows_out.append({
            "roster_id": rid,
            "name": names[rid]["name"] or f"Roster {rid}",
            "wins": rec["wins"], "losses": rec["losses"],
            "pf": rec["fpts"], "pa": round(pa.get(rid, 0.0), 1),
            "streak": at.get("streak"),
            "all_play": f"{apd['wins']}-{apd['losses']}" if apd else None,
            "points_rank": fpts_rank[rid],
            "efficiency": f"{eff['pct']:.0f}%" if eff else None,
            "efficiency_pct": eff["pct"] if eff else None,
            "left_on_bench": eff["left_on_bench"] if eff else None,
        })
    return rows_out, weeks_played


def _team_slugs(storage: Storage, league: League, names: dict[int, dict]) -> dict[int, str]:
    from leaguepage.draft_analysis import slugify

    used, out = set(), {}
    for r in storage.get_rosters(league.league_id):
        rid = r["roster_id"]
        nm = names[rid]["name"]
        base = slugify(nm) if nm else f"roster-{rid}"
        slug = base if base not in used else f"{base}-{rid}"
        used.add(slug)
        out[rid] = slug
    return out


def _comments_ctx(league: League, season: str, issue_key: str) -> dict | None:
    """giscus config for a published native issue page, or None. All values
    are public client-side config by design; imported historical archive
    pages never call this, so they stay read-only."""
    from leaguepage.config import COMMENTS

    c = COMMENTS
    if not (c.get("repo") and c.get("repo_id") and c.get("category_id")):
        return None
    term = f"{league.slug}:{season}:{issue_key}"
    if term in (c.get("disabled_issues") or []):
        return None
    return {"repo": c["repo"], "repo_id": c["repo_id"], "category": c.get("category", ""),
            "category_id": c["category_id"], "term": term,
            "theme": "dark" if league.theme == "disco" else "light"}


def _coalition_card(coalitions: dict, league: League, rid: int) -> dict | None:
    for c in coalitions.get("coalitions", []):
        m = c.get("roster_mapping") or {}
        if m.get("status") == "confirmed" and m.get("league") == league.slug and m.get("roster_id") == rid:
            idents = [coalitions["identities"][k] for k in c.get("members", [])
                      if coalitions.get("identities", {}).get(k, {}).get("status") == "confirmed"]
            flags = "".join(FLAGS.get((i.get("nationality") or "").split(" / ")[-1], "")
                            for i in idents)
            roles = " · ".join(i.get("role", "") for i in idents if i.get("role"))
            return {"label": f"{c['name']} coalition", "flags": flags, "roles": roles}
    return None


def _published_module_sections(snaps: list[dict], module_key: str) -> list[dict]:
    out = []
    for snap in snaps:
        for s in snap["sections"]:
            if s["module_key"] == module_key:
                out.append({"issue_label": f"{snap['season']} {snap['issue_label']}",
                            "html": _render_md(s["content_md"])})
    return out


def build_league(
    storage: Storage,
    league: League,
    env: Environment,
    out_dir: Path,
    pages: list[str],
    warnings: list[str],
    *,
    published_dir: Path,
    editorial_dir: Path | None,
    preview_issue: str | None = None,
) -> None:
    league_data = storage.get_league(league.league_id) or {}
    season = str(league_data.get("season") or "")
    week = int(storage.get_meta("current_week") or 1)
    names = resolve_public_names(storage, league)
    for rid, v in names.items():
        if v["name"] is None:
            warnings.append(f"{league.slug}: roster {rid} has no confirmed public "
                            "display name; shown as 'Roster N' on data pages and "
                            "blocking any issue publication.")
    slugs = _team_slugs(storage, league, names)
    coalitions = load_coalitions()
    snaps = _load_snapshots(league, published_dir)

    og = OG_IMAGES.get(league.slug)

    public_by_rid = {rid: (names[rid]["name"] or f"Roster {rid}") for rid in slugs}

    def render(rel: str, template: str, depth: int, *,
               description: str = "", **ctx) -> None:
        # lroot is the prefix from this page back to the LEAGUE root. Derive it
        # from the output path itself; the historical depth argument counted
        # from the site root and left every relative link one level too high.
        lroot = "../" * rel.count("/")
        # Every page gets the name-to-team-page map, built against its own
        # relative root. A name we cannot resolve renders as text.
        team_links = {nm: f"{lroot}team/{slugs[rid]}/index.html"
                      for rid, nm in public_by_rid.items()}
        html = env.get_template(template).render(
            league=league, season=season, lroot=lroot, team_links=team_links,
            css_name=league.slug,
            # Every page carries the league's team slugs so the My Team nav
            # shortcut can tell a live choice from a stale one. It used to
            # look for a card with that slug, and the cards only exist on
            # the home page, so the shortcut was hidden on the other 96.
            team_slugs=" ".join(sorted(slugs[rid] for rid in public_by_rid)),
            page_description=description,
            canonical=f"{SITE_URL}/{league.slug}/{rel}",
            og_image=f"{SITE_URL}/assets/{og[0]}" if og else None,
            og_image_width=og[1] if og else None,
            og_image_height=og[2] if og else None,
            **ctx)
        _write(out_dir, f"{league.slug}/{rel}", html, pages)

    # issue permalinks from frozen snapshots
    latest_ctx = None
    for snap in snaps:
        ctx = _issue_ctx(snap)
        if latest_ctx is None:
            latest_ctx = ctx
        render(f"{snap['season']}/{snap['issue_key']}/index.html", "public/issue_page.html",
               2, description=_issue_description(ctx, league), og_type="article",
               issue=ctx, current_nav="Archive",
               comments=_comments_ctx(league, snap["season"], snap["issue_key"]))

    # optional commissioner preview of an unpublished issue (dist-preview only)
    if preview_issue:
        from leaguepage.issue_builder import assemble_issue

        wk = int(preview_issue.removeprefix("week-")) if preview_issue.startswith("week-") else None
        assembled = assemble_issue(storage, league, season, preview_issue,
                                   base_dir=editorial_dir, week=wk)
        sections = [
            {"module_key": s["module_key"], "title": s["title"],
             "content_md": strip_editorial_comments(s["content_md"]).strip(),
             "credit": "by the Commissioner" if s["module_key"] == "lowdown" else None}
            for s in assembled["sections"] if s["kind"] != "auto" and s.get("content_md")
        ]
        snap = {"league": league.slug, "season": season, "issue_key": preview_issue,
                "issue_label": preview_issue.replace("week-", "Week ").replace("draft", "Draft Issue"),
                "published_at": None, "sections": sections,
                "href": f"{season}/{preview_issue}/index.html"}
        ctx = _issue_ctx(snap, preview=True)
        if latest_ctx is None:
            latest_ctx = ctx
        render(f"{season}/{preview_issue}/index.html", "public/issue_page.html",
               2, description=_issue_description(ctx, league), og_type="article",
               issue=ctx, current_nav=None)
        warnings += [f"{league.slug}: preview build includes UNPUBLISHED issue "
                     f"'{preview_issue}' — never deploy this output."]

    # matchups (Common Tactical Picture)
    computed = compute_week(storage, league, week)
    cards = []
    if computed:
        root = week_dir(league, season, week, editorial_dir)
        order = ("FEATURE", "MAJOR", "STANDARD", "CAPSULE")
        for sm in computed["scored"]:
            m = sm["matchup"]
            draft_path = root / "matchups" / m["matchup_slug"] / "draft.md"
            text = draft_path.read_text(encoding="utf-8") if draft_path.exists() else ""
            status = matchup_status(sm["state"], bool(text))
            approved = (status in ("approved", "locked") and text
                        and not any(b in text for b in BLOCKED_MARKERS))
            a, b = m["teams"]
            names_line = " vs ".join(
                names[t["roster_id"]]["name"] or f"Roster {t['roster_id']}" for t in (a, b))
            rec = (f"{a['record']['wins']}-{a['record']['losses']} vs "
                   f"{b['record']['wins']}-{b['record']['losses']}")
            score = (f"{a['points']:g} – {b['points']:g}"
                     if a.get("points") is not None and (a["points"] or b["points"]) else None)
            cards.append({
                # The site has two slug sources, and they disagreed: team
                # pages live at /team/all-barkley-no-bite-sealed/ while the
                # matchup anchor for the same team read "roster-6", because
                # the packet slug is derived before public names resolve. A
                # reader sharing "#roster-10-vs-roster-11" is sharing an
                # internal id. Use the anchor the rest of the site uses.
                "packet_slug": m["matchup_slug"],
                "anchor": "-vs-".join(
                    slugs.get(t["roster_id"], f"roster-{t['roster_id']}")
                    for t in (a, b)),
                "names": names_line, "records": rec,
                "score": score, "tags": sm["tags"],
                "prominence": (sm["state"] or {}).get("prominence_override") or sm["recommended_prominence"],
                "preview_html": _render_md(text) if approved else None,
            })
        cards.sort(key=lambda c: order.index(c["prominence"]))
    # The Common Tactical Picture page renders further down, once the
    # positional profile, transactions and draft recaps exist: a matchup with
    # no approved preview gets a computed Scout View instead of the words
    # "Preview pending", and Scout View needs all three.

    # standings (+ restrained analysis: movers, form, playoff picture)
    standings, weeks_played = _standings_rows(storage, league, names, week)
    from leaguepage.team_analytics import (
        playoff_outlook as _po, recent_form as _rf,
        scoring_streaks as _ss, snapshot_deltas as _sd,
    )

    st_analysis = {"movers": [], "hot": [], "trouble": [], "playoff": None}
    _deltas = _sd(storage, league, season, weeks_played)
    for rid, notes in _deltas.items():
        for n_ in notes:
            if n_.startswith("standings"):
                st_analysis["movers"].append(
                    f"{names[rid]['name'] or f'Roster {rid}'}: {n_}")
    _form = _rf(storage, league, week)
    _streaks = _ss(storage, league, week)
    if _form:
        n_teams = len(_form)
        for rid, f_ in _form.items():
            nm_ = names[rid]["name"] or f"Roster {rid}"
            if f_["rank"] <= 2 or (_streaks.get(rid, {}).get("kind") == "top-half scoring"):
                st_analysis["hot"].append(f"{nm_}: #{f_['rank']} scoring over the last {f_['window_label']}")
            if f_["rank"] >= n_teams - 1 or (_streaks.get(rid, {}).get("kind") == "bottom-half scoring"):
                st_analysis["trouble"].append(f"{nm_}: #{f_['rank']} of {n_teams} over the last {f_['window_label']}")
    _outlook = _po(storage, league, week)
    if _outlook.get("stage") == "too_early":
        st_analysis["playoff"] = {"note": _outlook["note"],
                                  "spots": _outlook["playoff_teams"]}
    elif "teams" in _outlook:
        rows_ = sorted(_outlook["teams"].items(), key=lambda kv: -kv[1]["odds"])
        st_analysis["playoff"] = {
            "spots": _outlook["playoff_teams"], "stage": _outlook["stage"],
            "rows": [{"name": names[rid]["name"] or f"Roster {rid}",
                      "band": t_["band"],
                      "odds": (f"{t_['odds']:.0%}" if _outlook["stage"] == "percentages"
                               else None)}
                     for rid, t_ in rows_],
            "note": _outlook.get("note")}
    # The table already carried all-play. The gap between a record and the
    # record its scoring earned is the number the league argues about, and
    # nobody computes it in their head from two columns.
    from leaguepage.reality_check import reality_check

    _pub = {rid: v["name"] or f"Roster {rid}" for rid, v in names.items()}
    reality = reality_check(storage, league, week, _pub, slugs)
    render("standings/index.html", "public/standings.html", 2,
           description=(f"Records, points for and against, all-play and lineup "
                        f"efficiency for every {league.display_name} team, "
                        f"{_through(weeks_played)}."),
           standings=standings, weeks_played=weeks_played, reality=reality,
           analysis=st_analysis, current_nav="Standings")

    # power (commissioner ranking; published-safe only)
    ranking_ctx, label = [], None
    for candidate_label in ([f"week-{week:02d}", "preseason"]):
        rows = storage.get_power_rankings(league.slug, season, candidate_label)
        if rows and not any(any(bm in (r.get("note") or "") for bm in BLOCKED_MARKERS) for r in rows):
            issue_key = "draft" if candidate_label == "preseason" else candidate_label
            issue = storage.get_issue(league.slug, season, issue_key)
            if issue and issue["status"] == "published":
                label = candidate_label
                tiers = {1: "Peer Competition", 2: "Near-Peer Competition",
                         3: "Competitive but Flawed", 4: "Strategic Reassessment Required"}
                prev = {p["roster_id"]: p for p in storage.get_power_rankings(
                    league.slug, season, "preseason")} if candidate_label != "preseason" else {}
                for r in rows:
                    rid = r["roster_id"]
                    p = prev.get(rid)
                    movement = None
                    if p and p.get("rank") and r.get("rank"):
                        diff = p["rank"] - r["rank"]
                        movement = f"▲{diff}" if diff > 0 else (f"▼{-diff}" if diff < 0 else "–")
                    rec = next((s for s in standings if s["roster_id"] == rid), {})
                    ranking_ctx.append({
                        "rank": r["rank"], "name": names[rid]["name"] or f"Roster {rid}",
                        "movement": movement, "tier_name": tiers.get(r.get("tier")),
                        "record": f"{rec.get('wins', 0)}-{rec.get('losses', 0)} · {rec.get('pf', 0)} PF",
                        "note": r.get("note"),
                        "roster_id": rid,
                    })
                break
    ranking_source = None
    if not ranking_ctx:
        # He publishes the ranking as prose in the Draft Issue and nothing
        # ever put it in the power_rankings table, so the page showed the
        # model board alone and the most interesting argument on the site
        # sat two clicks away with nothing pointing at it. Read the order he
        # published.
        from leaguepage.published_ranking import extract_ranking

        toks = {rid: {t.strip("()'\u2019.,").lower()
                      for t in (names[rid]["name"] or "").split()}
                for rid in names}
        pub_names = {rid: names[rid]["name"] or f"Roster {rid}" for rid in names}
        for snap in snaps:
            found = extract_ranking(snap["sections"], league_slug=league.slug,
                                    season=snap["season"], issue_key=snap["issue_key"],
                                    name_tokens=toks, public_names=pub_names)
            if not found:
                continue
            label = snap["issue_label"]
            ranking_source = {"label": snap["issue_label"],
                              "section": found["section_title"],
                              "corrected": found.get("corrected"),
                              "href": f"{snap['href']}#{found['anchor']}"
                              if found["anchor"] else snap["href"]}
            for row in found["rows"]:
                rid = row["roster_id"]
                rec = next((s_ for s_ in standings if s_["roster_id"] == rid), {})
                ranking_ctx.append({
                    "rank": row["rank"], "name": row["name"], "movement": None,
                    "tier_name": None,
                    # preseason, "0-0 . 0.0 PF" on every row is just noise
                    "record": (f"{rec.get('wins', 0)}-{rec.get('losses', 0)} "
                               f"\u00b7 {rec.get('pf', 0)} PF"
                               if weeks_played else None),
                    # What he actually wrote about them. The page had a slot
                    # for this and was passing None into it.
                    "note": row.get("note") or None, "roster_id": rid,
                })
            break
    # Peer and Near-Peer renders below, once the positional profile exists:
    # with no published Commissioner ranking the page shows the Model Board
    # rather than "No ranking has been published yet this season."

    # teams index + team pages
    published_awards = []
    for snap in snaps:
        d = storage.get_award_decisions(league.slug, snap["season"], snap["issue_key"])
        for key, dec in d.items():
            if dec["decision"] in ("awarded", "manual"):
                published_awards.append({"award_key": key, "winner": dec.get("winner"),
                                         "issue": f"{snap['season']} {snap['issue_label']}"})
    scores = weekly_scores(storage, league.league_id, MAX_SCAN_WEEK)
    ap = all_play(scores)

    # deterministic team analytics: positional strength, form, outlook
    from leaguepage.team_analytics import (
        label_for_rank, playoff_outlook, positional_profile,
        recent_form, scoring_streaks, snapshot_deltas, strengths_weaknesses,
        team_outlook,
    )

    weeks_played_league = max((len(v) for v in scores.values()), default=0)
    profile = positional_profile(storage, league, weeks_played=weeks_played_league)
    outlook = playoff_outlook(storage, league, week)
    form = recent_form(storage, league, week)
    streak_map = scoring_streaks(storage, league, week)
    deltas = snapshot_deltas(storage, league, season, weeks_played_league)

    # transaction rationale: Force Flow reading + team Key Moves. The
    # internal confidence field never reaches a template context.
    from leaguepage.transaction_analysis import analyze_transactions

    tx_rows = analyze_transactions(storage, league, MAX_SCAN_WEEK)
    tx_by_rid: dict[int, list[dict]] = {}
    for _row in tx_rows:
        if _row.get("significant"):
            for _rid in _row["rids"]:
                tx_by_rid.setdefault(_rid, []).append(_row)

    def _move_ctx(row: dict) -> dict:
        from leaguepage.transaction_analysis import describe_move

        return {"week": row["week"], "type": row["type"],
                "line": describe_move(row),
                "adds": ", ".join(a["name"] for a in row["adds"]),
                "drops": ", ".join(d["name"] for d in row["drops"]),
                "faab": row["faab"], "priority": row.get("priority", 0),
                "text": row["rationale"]["text"],
                "questionable": row["rationale"]["kind"] == "questionable",
                "rank_shift": row.get("rank_shift"),
                "outcome": row.get("outcome"),
                # what actually happened afterwards, as its own column. The
                # rationale above is what he was reading at the time and is
                # never rewritten with hindsight.
                "aged": row.get("aged_line")}

    from leaguepage.draft_aging import (aging_line, departed_headliners,
                                        draft_aging)
    from leaguepage.draft_aging import team_summary as aging_summary

    aging_by_rid = draft_aging(storage, league)

    # draft market value: shared by the draft page and team Draft Recaps
    from leaguepage.adp import load_adp_for_league
    from leaguepage.draft_analysis import analyze_league_draft
    from leaguepage.draft_value import (
        classify_pick, headline_deviations, position_order_context,
    )

    _adp = load_adp_for_league(league)
    draft_analysis = analyze_league_draft(storage, league, managers={}, adp=_adp)
    league_size = (draft_analysis or {}).get("total_teams") or profile["n"]
    analysis_picks = (draft_analysis or {}).get("picks") or []
    recap_by_rid: dict[int, dict] = {}
    if draft_analysis:
        for t in draft_analysis["teams"]:
            _picks = [dict(p, dv=classify_pick(p.get("delta"), league_size,
                                             off_board=p.get("off_board", False)))
                      for p in t["picks_by_round"]]
            # Headline Reach/Steal are skill positions only, matching the
            # Draft page: overall ECR ranks every K and DST below the
            # draftable range while lineups force everyone to draft one, so
            # a kicker's 80-pick "reach" measures the reference board's
            # shape, not a roster decision. It was headlining team pages as
            # the Biggest Reach, which is the calibration decision leaking.
            _hd = headline_deviations(t["picks_by_round"], league_size, top=1)
            _st = [dict(p, dv=classify_pick(p["delta"], league_size,
                                        off_board=p.get("off_board", False)),
                        context=position_order_context(_adp, analysis_picks, p))
                   for p in _hd["special_teams"][:2]]
            _aged = aging_by_rid.get(t["roster_id"]) or []
            _labels = {}
            for _h, _lab in ((_hd["skill_reaches"], "REACH"),
                             (_hd["skill_steals"], "STEAL")):
                for _p in _h[:1]:
                    _labels[_p["name"]] = _lab
            recap_by_rid[t["roster_id"]] = {
                "aging": aging_summary(_aged) if _aged else None,
                # Never a re-grade: REACH and STEAL are the market call made
                # on the night and stay exactly as they were. This says only
                # whether the player is still here.
                "departed": [dict(r, line=aging_line(r))
                             for r in departed_headliners(_aged, _labels)],
                "picks": _picks,
                "biggest_reach": (dict(_hd["skill_reaches"][0],
                                       dv=classify_pick(_hd["skill_reaches"][0]["delta"],
                                                        league_size))
                                  if _hd["skill_reaches"] else None),
                "biggest_steal": (dict(_hd["skill_steals"][0],
                                       dv=classify_pick(_hd["skill_steals"][0]["delta"],
                                                        league_size))
                                  if _hd["skill_steals"] else None),
                "special_teams": _st,
            }

    # Common Tactical Picture: a Commissioner preview when one is approved,
    # a computed Scout View when there is not, and nothing at all when the
    # matchup genuinely has nothing to say for itself.
    from leaguepage.history import matchup_history
    from leaguepage.model_views import scout_view
    from leaguepage.receipts import receipts_for_matchup

    from leaguepage.leverage import describe_stake, week_leverage
    from leaguepage.team_analytics import format_odds

    # What this week's games are actually worth. Returns None until the
    # table means something and the schedule is known, and says nothing
    # about a team whose result barely moves its own odds.
    leverage = week_leverage(storage, league, week) or {}
    leverage_teams = leverage.get("teams") or {}

    moves_ctx_by_rid = {rid: [_move_ctx(r) for r in rows]
                        for rid, rows in tx_by_rid.items()}
    public_names = {rid: v["name"] or f"Roster {rid}" for rid, v in names.items()}
    if computed:
        # Alias-aware, so history.py can drop an archive quote carrying a
        # private alias and pick the next candidate instead of poisoning the
        # build audit two stages later.
        handles = _private_handles(sorted(public_names.values()))
        by_anchor = {sm["matchup"]["matchup_slug"]: sm for sm in computed["scored"]}
        for card in cards:
            sm = by_anchor.get(card["packet_slug"])
            card["scout"] = (
                None if card["preview_html"] or not sm else
                scout_view(sm["matchup"], profile=profile, names=public_names,
                           tags=card["tags"], moves_by_rid=moves_ctx_by_rid,
                           recap_by_rid=recap_by_rid))
            # History runs regardless of whether a preview exists: the
            # Commissioner's prose and the receipts are complementary.
            card["history"] = matchup_history(
                storage, league, season, week, sm["matchup"],
                sm.get("story_memory") or {}, public_names,
                private_handles=handles) if sm else []
            # One tracked take involving either side, at most. A matchup card
            # carrying three old quotes is a scrapbook, not a callback.
            stakes = []
            for t in (sm["matchup"]["teams"] if sm else []):
                lv = leverage_teams.get(t["roster_id"])
                if not lv or not lv["material"]:
                    continue
                stakes.append({
                    "name": public_names.get(t["roster_id"], ""),
                    "slug": slugs.get(t["roster_id"]),
                    "line": (f"{format_odds(lv['if_win'])} to make the playoffs "
                             f"with a win, {format_odds(lv['if_lose'])} with a loss"),
                    "verdict": describe_stake(lv["if_win"], lv["if_lose"]),
                })
            card["stakes"] = stakes
            card["past_statement"] = receipts_for_matchup(
                storage, league, season, week, names,
                [t["roster_id"] for t in sm["matchup"]["teams"]]) if sm else None
    render("matchups/index.html", "public/matchups.html", 2,
           description=(f"Week {week} in {league.display_name}: {len(cards)} "
                        f"matchup{'' if len(cards) == 1 else 's'}, what each one "
                        f"turns on and what is at stake."),
           cards=cards, week=week, current_nav="Common Tactical Picture")

    # Peer and Near-Peer: the Commissioner's ranking is authoritative when it
    # exists; the Model Board fills the page when it does not, and stays on
    # as a comparison column when it does, because the disagreement is the
    # entertaining part.
    from leaguepage.model_views import compare_to_commissioner, model_board

    _adp_src = load_adp_for_league(league)
    board = model_board(source=(_adp_src.provenance_label() if _adp_src else None),
                        profile=profile,
                        names={rid: v["name"] or f"Roster {rid}"
                               for rid, v in names.items()},
                        slugs=slugs, standings=standings, form=form,
                        weeks_played=weeks_played_league)
    disagreements_ctx, disagree_floor = [], 0
    if ranking_ctx:
        ranking_ctx = compare_to_commissioner(board, ranking_ctx)
        for row in ranking_ctx:
            row["slug"] = slugs.get(row.get("roster_id"))
        from leaguepage.published_ranking import disagreements

        disagreements_ctx, disagree_floor = disagreements(ranking_ctx)
    render("power/index.html", "public/power.html", 2,
           description=(f"Where every {league.display_name} team ranks, "
                        f"{_through(weeks_played_league)} \u2014 and where the "
                        f"Commissioner and the model disagree."),
           rankings=ranking_ctx, label=label, board=board,
           ranking_source=ranking_source, disagreements=disagreements_ctx,
           disagree_floor=disagree_floor,
           current_nav="Peer and Near-Peer")

    # Team pages: a personal briefing on top, editorial weighting under it,
    # and a section order that ages the draft down the page as results
    # accumulate.
    from leaguepage.front_page import season_state
    from leaguepage.pubqa import _norm_tokens
    from leaguepage.team_briefing import (
        build as build_briefing, editorial_strengths, league_mentions,
        section_order,
    )

    front_state = season_state(
        weeks_played_league, week,
        (league_data.get("settings") or {}).get("playoff_week_start"))
    name_tokens = {rid: _norm_tokens(v["name"]) for rid, v in names.items()
                   if v["name"]}
    # Tracked takes first, archive-derived receipts after; receipts_for_team
    # applies that order and caps the list.
    from leaguepage.receipts import receipts_for_team

    team_receipts_by_rid = {
        rid: receipts_for_team(storage, league, season, week, snaps, names, rid)
        for rid in names}

    # One compact card per team for the "Your week" module. The build cannot
    # know whose browser this is, so every card ships and the client reveals
    # one; the alternative is an account, and this product does not want one.
    myteam_cards: list[dict] = []
    team_cards = []
    for r in storage.get_rosters(league.league_id):
        rid = r["roster_id"]
        rec = team_record(r)
        nm = names[rid]["name"] or f"Roster {rid}"
        st = next((i + 1 for i, s in enumerate(standings) if s["roster_id"] == rid), None)
        team_cards.append({"slug": slugs[rid], "name": nm,
                           "record": f"{rec['wins']}-{rec['losses']}", "standing": st,
                           "model_rank": next((row["rank"] for row in board["rows"]
                                               if row["roster_id"] == rid), None),
                           "coalition": _coalition_card(coalitions, league, rid),
                           "pos_ranks": {pos: profile["ranks"][pos][rid]
                                         for pos in profile["positions"]}})
        roster_players = []
        for pid in (r.get("players") or []):
            p = storage.get_player(pid) or {}
            roster_players.append({"name": p.get("full_name") or pid,
                                   "position": p.get("position") or "?",
                                   "nfl": p.get("team") or "FA"})
        roster_players.sort(key=lambda p: ({"QB": 0, "RB": 1, "WR": 2, "TE": 3,
                                            "K": 4, "DEF": 5}.get(p["position"], 6), p["name"]))
        apd = ap.get(rid)
        awards = [{"name": AWARD_NAMES.get(a["award_key"], a["award_key"]), "issue": a["issue"]}
                  for a in published_awards
                  if a.get("winner") in (slugs[rid], nm)]
        mentions = league_mentions(snaps, nm, rid, name_tokens)
        # "Last time we talked about you" was false while the only issue on
        # file is the current one, and it is machine copy borrowing his
        # first person. The heading now says which case it is.
        newest = snaps[0]["issue_key"] if snaps else None
        for _m in mentions:
            _m["current"] = bool(newest and _m.get("issue_key") == newest)
        sw = strengths_weaknesses(profile, rid)
        positional_rows = []
        for pos in profile["positions"]:
            rank = profile["ranks"][pos][rid]
            t = profile["teams"][rid][pos]
            nuance = None
            if t["fragility"] >= 0.6 and t["count"] > 1:
                nuance = f"{int(t['fragility'] * 100)}% of the room is {t['top_player']}"
            elif (profile["starter_ranks"][pos][rid] <= round(0.4 * profile["n"])
                  and profile["depth_ranks"][pos][rid] >= round(0.8 * profile["n"])):
                nuance = "starters carry it; depth is thin"
            positional_rows.append({"pos": pos, "rank": rank, "n": profile["n"],
                                    "label": label_for_rank(rank, profile["n"]),
                                    "nuance": nuance})
        trend_lines = []
        if form and rid in form:
            f = form[rid]
            trend_lines.append(f"#{f['rank']} scoring over the last {f['window_label']}")
        if rid in streak_map:
            s_ = streak_map[rid]
            trend_lines.append(f"{s_['length']} straight weeks of {s_['kind']}")
        trend_lines += deltas.get(rid, [])[:2]
        playoff_line = None
        if outlook.get("stage") == "too_early":
            playoff_line = outlook["note"]
        elif "teams" in outlook and rid in outlook["teams"]:
            t_ = outlook["teams"][rid]
            playoff_line = (t_["band"] if outlook["stage"] == "bands"
                            else f"{t_['odds']:.0%} ({t_['band']})")
        moves_ctx = [_move_ctx(m) for m in tx_by_rid.get(rid, [])[-3:]]
        from leaguepage.leverage import rooting_interest

        rooting = []
        for r_ in rooting_interest(storage, league, week, rid):
            rooting.append({
                "for": public_names.get(r_["root_for"], ""),
                "for_slug": slugs.get(r_["root_for"]),
                "against": public_names.get(r_["against"], ""),
                "against_slug": slugs.get(r_["against"]),
                "swing": f"{r_['swing']:.0%}",
            })
        e_str, e_weak = editorial_strengths(profile, rid, sw)
        model_rank = next((row["rank"] for row in board["rows"]
                           if row["roster_id"] == rid), None)
        # exact side match, never a substring: "Dave" lives inside plenty of
        # other strings
        def _opponent(card_names: str) -> dict:
            """The other side of this week's game, as a link.

            A team page told a reader who they play and gave them no way to
            go and look at them; the only link on the row went to the matchup
            board they had probably just come from.
            """
            other = next((x for x in card_names.split(" vs ") if x != nm), "")
            other_rid = next((r for r, n2 in public_names.items() if n2 == other),
                             None)
            return {"name": other, "slug": slugs.get(other_rid) if other_rid else None}

        next_card = next(({"names": c["names"], "anchor": c["anchor"],
                           "opponent": _opponent(c["names"]),
                           # The link beside this now says "on the board",
                           # so the note saying it too read "week 1 on the
                           # board, on the board".
                           "note": (", ".join(c["tags"]) if c["tags"] else
                                    ("final" if c["score"] else f"week {week}"))}
                          for c in cards if nm in c["names"].split(" vs ")), None)
        briefing = build_briefing(
            # Preseason standings position is an arbitrary tiebreak among 0-0
            # teams, so the briefing shows the model board's rank instead.
            state=front_state, name=nm, record=rec,
            standing=st if weeks_played_league else model_rank,
            weeks_played=weeks_played_league, profile=profile, rid=rid,
            form=(form or {}).get(rid), streak=streak_map.get(rid),
            all_play=apd,
            playoff_line=playoff_line if weeks_played_league else None,
            playoff_delta=next((d for d in deltas.get(rid, [])
                                if d.startswith("playoff")), None),
            key_moves=moves_ctx, next_matchup=next_card,
            deltas=deltas.get(rid, []), receipts=team_receipts_by_rid.get(rid, []))
        myteam_cards.append({
            "slug": slugs[rid], "name": nm,
            "storyline": briefing.get("storyline"),
            "position": briefing.get("position"),
            "playoff": briefing.get("playoff"),
            "strength": briefing.get("strength"),
            "weakness": briefing.get("weakness"),
            "key_move": (briefing.get("key_move") or {}).get("line"),
            "next": briefing.get("next"),
            "stake": _own_stake(leverage_teams.get(rid), public_names, next_card),
            "receipt": (briefing.get("receipts") or [None])[0],
        })
        render(f"team/{slugs[rid]}/index.html", "public/team.html", 3,
               description=(f"{nm}: {rec['wins']}-{rec['losses']}, "
                            f"{rec['fpts']:g} points for, positional room ranks, "
                            f"draft recap and what changed."),
               team={"name": nm, "record": f"{rec['wins']}-{rec['losses']}",
                     "standing": st, "pf": rec["fpts"],
                     "co_managed": bool(r.get("co_owners")),
                     "coalition": _coalition_card(coalitions, league, rid),
                     "score_history": scores.get(rid, []),
                     "all_play": f"{apd['wins']}-{apd['losses']}" if apd else None,
                     "awards": awards, "roster": roster_players,
                     "mentions": mentions,
                     "positional": positional_rows,
                     "strengths": e_str,
                     "weaknesses": e_weak,
                     # Preseason team_outlook is exactly the strengths and
                     # weaknesses already printed above it; only show it once
                     # it carries something those lines do not.
                     "outlook_signals": [
                         s_ for s_ in team_outlook(storage, league, season, rid,
                                                   week, profile=profile)
                         if s_ not in e_str and s_ not in e_weak],
                     "trend": trend_lines,
                     "playoff": playoff_line,
                     "key_moves": moves_ctx,
                     "draft_recap": recap_by_rid.get(rid),
                     "league_size": league_size,
                     "briefing": briefing,
                     "sections": section_order(front_state),
                     "rooting": rooting,
                     # Disco drafts no kickers or defenses, so the K/DEF
                     # disclosure was boilerplate from the other league,
                     # printed twelve times.
                     "has_special_teams": any(
                         p_ not in SKILL_POSITIONS for p_ in profile["positions"]),
                     "own_stake": _own_stake(leverage_teams.get(rid),
                                             public_names, next_card),
                     "stage": profile["stage"]},
               current_nav=None)
    render("teams/index.html", "public/teams.html", 2,
           weeks_played=weeks_played_league,
           description=(f"Every roster in {league.display_name} side by side, "
                        f"ranked room by room across "
                        f"{', '.join(profile['positions'])}."),
           teams=team_cards, positions=profile["positions"],
           stage=profile["stage"], current_nav="Teams")

    # transactions (Force Flow): analytical reading + full reference log
    log, meaningful = [], []
    for row in tx_rows:
        team_label = ", ".join(names[rid]["name"] or f"Roster {rid}"
                               for rid in row["rids"])
        ctx = dict(_move_ctx(row), team=team_label)
        log.append(ctx)
        if row.get("significant"):
            meaningful.append(ctx)
    meaningful.sort(key=lambda m: -m["priority"])
    # The reading of the moves is arithmetic over synced data, not a
    # language model, so it says that rather than wearing a Claude badge it
    # has not earned.
    ff_prov = provenance.describe_machine("transactions") if meaningful else None
    render("transactions/index.html", "public/transactions.html", 2,
           force_flow_provenance=ff_prov,
           description=(f"Every completed add, drop and trade in "
                        f"{league.display_name}, with what each move was reading "
                        f"and what it cost."),
           log=log, meaningful=meaningful,
           editorial_sections=_published_module_sections(snaps, "forceflow"),
           current_nav="Force Flow")

    # draft page
    analysis = draft_analysis
    board, team_sections, status_line, prov = [], [], "", None
    reaches_ctx, steals_ctx, st_ctx = [], [], []
    if analysis and analysis["picks"]:
        prov = analysis.get("adp_provenance")
        status_line = (f"{analysis['pick_count']} of {analysis.get('expected_pick_count') or '?'} picks "
                       f"({analysis['draft_status']}); {analysis['total_teams']} teams, "
                       f"{analysis['rounds']} rounds, {analysis['draft_type']}.")
        rid_by_slug = {t["team_slug"]: t["roster_id"] for t in analysis["teams"]}
        public_of = {t["team_slug"]: (names[rid_by_slug[t["team_slug"]]]["name"]
                                      or f"Roster {rid_by_slug[t['team_slug']]}")
                     for t in analysis["teams"]}
        for p in analysis["picks"]:
            board.append({**{k: p[k] for k in ("pick_no", "round", "name", "position",
                                               "nfl_team", "adp", "delta")},
                          "team": public_of.get(p["team_slug"], p["team_slug"]),
                          "team_slug": p["team_slug"],
                          "dv": classify_pick(p["delta"], league_size,
                                              off_board=p.get("off_board", False))})
        for t in analysis["teams"]:
            team_sections.append({
                # Two different slugs, on purpose. `slug` is the draft
                # analysis's own and is the in-page anchor. `page_slug` is
                # the site's team-page slug, and using the first one for the
                # href sent all 22 of these links to a 404.
                "slug": t["team_slug"], "name": public_of[t["team_slug"]],
                "page_slug": slugs.get(t["roster_id"]),
                "position_counts": ", ".join(f"{n} {pos}" for pos, n in t["position_counts"].items()),
                "picks": recap_by_rid.get(t["roster_id"], {}).get("picks", []),
            })

        def _headline_ctx(p: dict, *, st_context: bool = False) -> dict:
            from leaguepage.draft_value import position_order_context

            return {"name": p["name"], "pick_no": p["pick_no"], "adp": p["adp"],
                    "team": public_of.get(p["team_slug"], p["team_slug"]),
                    "team_slug": p["team_slug"],
                    "dv": classify_pick(p["delta"], league_size,
                                        off_board=p.get("off_board", False)),
                    "context": (position_order_context(
                        load_adp_for_league(league), analysis["picks"], p)
                        if st_context else None)}

        from leaguepage.draft_value import headline_deviations

        hd = headline_deviations(analysis["picks"], league_size)
        reaches_ctx = [_headline_ctx(p) for p in hd["skill_reaches"]]
        steals_ctx = [_headline_ctx(p) for p in hd["skill_steals"]]
        st_ctx = [_headline_ctx(p, st_context=True)
                  for p in hd["special_teams"]]
    recap = next((s for s in snaps if s["issue_key"] == "draft"), None)
    render("draft/index.html", "public/draft.html", 2,
           description=_draft_description(league, analysis),
           board=board, team_sections=team_sections, status_line=status_line,
           reaches=reaches_ctx, steals=steals_ctx, special_teams=st_ctx,
           league_size=league_size,
           adp_provenance=prov, recap_href=recap["href"] if recap else None,
           current_nav="Draft")

    # black box
    records = []
    all_rows = [(rid, wk, pts) for rid, rows in scores.items() for wk, pts in rows]
    weeks_seen = sorted({wk for _, wk, _ in all_rows})
    population = (f"weeks {weeks_seen[0]}-{weeks_seen[-1]}, {season} season, this league only"
                  if len(weeks_seen) >= 3 else None)
    if population:
        hi = max(all_rows, key=lambda x: x[2])
        lo = min(all_rows, key=lambda x: x[2])
        records = [
            {"id": "high-week", "label": "Highest single-week score",
             "holder": names[hi[0]]["name"] or f"Roster {hi[0]}",
             "value": f"{hi[2]:g}", "when": f"week {hi[1]}"},
            {"id": "low-week", "label": "Lowest single-week score",
             "holder": names[lo[0]]["name"] or f"Roster {lo[0]}",
             "value": f"{lo[2]:g}", "when": f"week {lo[1]}"},
        ]
        # The interesting question about a record is who is near it. A table
        # of settled marks is a leaderboard footnote; a mark with somebody
        # four points off it is a thing to watch on Sunday.
        latest_wk = max(wk for _, wk, _ in all_rows)
        latest = [(rid, pts) for rid, wk, pts in all_rows if wk == latest_wk]
        if latest:
            best = max(latest, key=lambda x: x[1])
            if best[1] < hi[2]:
                records.append({
                    "id": "closest", "label": "Closest to the high mark",
                    "holder": names[best[0]]["name"] or f"Roster {best[0]}",
                    "value": f"{hi[2] - best[1]:.1f} short",
                    "when": f"week {latest_wk}"})
        margins = []
        for wk in sorted({w for _, w, _ in all_rows}):
            by_mid: dict[int, list[tuple[int, float]]] = defaultdict(list)
            for row in storage.get_matchups(league.league_id, wk):
                if row.get("matchup_id") is not None and (row.get("points") or 0) > 0:
                    by_mid[row["matchup_id"]].append((row["roster_id"],
                                                      float(row["points"])))
            for pair in by_mid.values():
                if len(pair) == 2:
                    a, b = sorted(pair, key=lambda x: -x[1])
                    margins.append((abs(a[1] - b[1]), wk, a[0], b[0]))
        if margins:
            widest = max(margins)
            closest = min(margins)
            records.append({
                "id": "widest-margin", "label": "Widest margin",
                "holder": names[widest[2]]["name"] or f"Roster {widest[2]}",
                "value": f"{widest[0]:.1f}", "when": f"week {widest[1]}"})
            records.append({
                "id": "closest-margin", "label": "Narrowest margin",
                "holder": names[closest[2]]["name"] or f"Roster {closest[2]}",
                "value": f"{closest[0]:.1f}", "when": f"week {closest[1]}"})
    from leaguepage.model_views import black_box_preview

    render("black-box/index.html", "public/blackbox.html", 2,
           description=(f"Season records and standing marks in "
                        f"{league.display_name}, {_through(weeks_played_league)}."),
           records=records, population=population,
           archive_depth=_archive_depth(storage, league),
           watching=black_box_preview(profile=profile,
                                      names={rid: v["name"] or f"Roster {rid}"
                                             for rid, v in names.items()},
                                      reaches=reaches_ctx, steals=steals_ctx,
                                      weeks_played=weeks_played_league),
           editorial_sections=_published_module_sections(snaps, "blackbox"),
           current_nav="Black Box")

    # archive (published + imported historical, verbatim)
    scope = {"disco": [("disco", "Disco Chat — historical issues"),
                       ("daddy", "Big Daddy AF — predecessor-league issues")],
             "surfeit": []}.get(league.slug, [])
    historical_groups = []
    for slug_key, title in scope:
        items = [i for i in storage.list_archive_issues(slug_key)]
        by_season: dict[str, list[dict]] = defaultdict(list)
        for it in items:
            by_season[it["season"] or "Undated"].append(it)
        # Fourteen of forty-two Disco issues carry a title whose year is one
        # ahead of the season the file itself declares, so the archive listed
        # "2023 Disco Week 1" under a 2023 heading that was really 2022. The
        # masthead volume line agrees with the frontmatter, not the title, and
        # the archive is source data nobody should be silently rewriting -- so
        # the LABEL is the indexed season and week, and the original title
        # stays beside it when it disagrees.
        for rows_ in by_season.values():
            for it in rows_:
                it["label"] = _archive_label(it)
                # Only a title claiming a DIFFERENT year is worth showing.
                # A title with no year in it ("Disco 12") disagrees with
                # nothing, and printing it as a discrepancy is noise.
                years = re.findall(r"\b(20\d\d)\b", it.get("title") or "")
                it["title_differs"] = bool(
                    it.get("season") and years and it["season"] not in years)
        seasons = sorted(by_season.items(), key=lambda kv: kv[0], reverse=True)
        historical_groups.append({"title": title, "seasons": seasons})
        # Chronological, so previous and next mean what a reader expects.
        # Fifty-five issues with one link each is a corpus nobody can browse.
        ordered = sorted(items, key=lambda i: (i["season"] or "", i["week"] or 0,
                                               i["issue_id"]))
        for pos, it in enumerate(ordered):
            full = storage.get_archive_issue(it["issue_id"]) or {}
            paragraphs = [p.strip() for p in re.split(r"\n\s*\n", full.get("body") or "")
                          if p.strip()]
            origin = "Disco Chat" if slug_key == "disco" else "Big Daddy AF"

            def _sib(offset: int, _pos=pos, _all=ordered) -> dict | None:
                j = _pos + offset
                if not (0 <= j < len(_all)):
                    return None
                return {"title": _all[j]["title"],
                        "href": f"archive/a{_all[j]['issue_id']}/index.html"}

            render(f"archive/a{it['issue_id']}/index.html", "public/archive_issue.html", 3,
                   description=(f"{it['title']} \u2014 from the {origin} archive."),
                   og_type="article",
                   item={"title": it["title"], "season": it["season"], "week": it["week"],
                         "origin": origin, "paragraphs": paragraphs},
                   prev_issue=_sib(-1), next_issue=_sib(1),
                   current_nav="Archive")
    # Six seasons of titles Sleeper cannot reach, read out of the mastheads
    # that recorded them at the time. Names resolve to team pages only where
    # a CONFIRMED alias says who they are.
    from leaguepage.archive_records import (drop_private_names, ledger_note,
                                            resolve_handles, season_ledger)
    from leaguepage.editorial import load_managers, manager_for_roster

    _managers = load_managers()
    ledger = resolve_handles(
        drop_private_names(season_ledger(storage, league),
                           _private_handles(sorted(public_names.values()))),
        [{"roster_id": rid, "team_slug": slugs.get(rid),
          "manager_keys": manager_for_roster(_managers, league.slug, rid)}
         for rid in names],
        _managers)
    # Seasons Sleeper cannot reach, recovered from the records printed in
    # the previews rather than from any stated result. Inference, labelled
    # as inference; a season whose names are not all publishable does not
    # ship at all, because publishing half of it misstates every record.
    from leaguepage.archive_results import (coverage_note, drop_private_results,
                                            resolve_result_teams, season_results,
                                            standings_rows, title_tension,
                                            weeks_rows)

    _result_teams = [{"roster_id": rid, "team_slug": slugs.get(rid),
                      "manager_keys": manager_for_roster(_managers, league.slug, rid)}
                     for rid in names]
    reconstructed = []
    for _rec in drop_private_results(
            season_results(storage, league),
            _private_handles(sorted(public_names.values()))):
        _slugs = resolve_result_teams(_rec, _result_teams, _managers)
        _rows = standings_rows(_rec)
        reconstructed.append({
            "season": _rec["season"],
            "note": coverage_note(_rec),
            # Two independent readings of the same archive. Where they
            # disagree about who had the season, that is the story.
            "tension": title_tension(_rows, _rec["season"], ledger),
            "standings": _rows,
            "weeks": weeks_rows(_rec),
            "slugs": _slugs,
            "sources": sorted({i for r in _rec["results"] for i in r["from_issues"]}),
        })
    render("archive/index.html", "public/archive.html", 2,
           description=(f"Every issue of {league.display_name} on the record, "
                        f"newest first."),
           published=snaps, historical_groups=historical_groups,
           ledger=ledger, ledger_note=ledger_note(ledger),
           reconstructed=reconstructed,
           current_nav="Archive")

    # league home (front page) — season-state aware editorial hierarchy
    from leaguepage import front_page
    from leaguepage.receipts import front_page_receipt

    front_receipt = front_page_receipt(storage, league, season, week, snaps, names)
    # Nothing about a receipt may reach the build without passing the gate:
    # provenance, the paraphrase rule, private fields and stale identity are
    # blockers here exactly as they are inside an issue.
    from leaguepage import pubqa as _qa
    from leaguepage.takes import public_receipts as _pub_receipts

    _rq = _qa.build_context(storage, league, season, "public-receipts")
    _receipt_findings = _qa.check_receipts(
        [dict(r, presented_as_quote=r["verbatim"]) for r in
         _pub_receipts(storage, league, season, public_names)], _rq)
    for _f in _receipt_findings:
        if _f.severity == _qa.BLOCKER:
            raise RuntimeError(
                f"receipt gate: {_f.title} — {_f.detail}")
        warnings.append(f"{league.slug}: receipt warning — {_f.title}")
    front = front_page.build({
        "week": week,
        "weeks_played": weeks_played_league,
        "playoff_week_start": (league_data.get("settings") or {}).get("playoff_week_start"),
        "author_roster_id": league.author_roster_id,
        "last_sync": storage.get_meta("last_sync_at"),
        "names": {rid: v["name"] or f"Roster {rid}" for rid, v in names.items()},
        "slugs": slugs,
        "cards": cards,
        "moves": meaningful,
        "profile": profile,
        "form": form,
        "movers": st_analysis["movers"],
        "hot": st_analysis["hot"],
        "trouble": st_analysis["trouble"],
        "playoff": st_analysis["playoff"],
        "reaches": reaches_ctx,
        "steals": steals_ctx,
        "receipt": front_receipt,
    })
    # The five numbers from the last completed week that anybody would
    # repeat out loud. Facts, not awards: naming a winner is the
    # Commissioner's job and this build step does not do it for him.
    from leaguepage.week_leaders import week_leaders

    leaders_week = weeks_played_league
    leaders = (week_leaders(storage, league, leaders_week, public_names, slugs)
               if leaders_week else [])
    render("index.html", "public/home.html", 1,
           myteam=sorted(myteam_cards, key=lambda t: t["name"].lower()),
           leaders=leaders, leaders_week=leaders_week,
           description=_home_description(league, front, latest_ctx,
                                         weeks_played_league, week),
           latest=latest_ctx, standings=standings, published=snaps,
           front=front, current_nav="Home")


def build_site(
    storage: Storage,
    *,
    out_dir: Path | None = None,
    published_dir: Path | None = None,
    editorial_dir: Path | None = None,
    leagues: list[League] | None = None,
    preview_issues: dict[str, str] | None = None,
) -> dict:
    out = out_dir or DIST_DIR
    if out.exists():
        try:
            shutil.rmtree(out)
        except PermissionError:
            # Windows: a shell/CLI parked inside dist holds the root dir open.
            # Its contents still delete; clear them and reuse the directory.
            for child in out.iterdir():
                shutil.rmtree(child) if child.is_dir() else child.unlink()
    out.mkdir(parents=True, exist_ok=True)
    env = _env()
    pages: list[str] = []
    warnings: list[str] = []
    published_names: set[str] = set()
    for league in (leagues or LEAGUES):
        for v in resolve_public_names(storage, league).values():
            if v.get("name"):
                published_names.add(v["name"])
        build_league(storage, league, env, out, pages, warnings,
                     published_dir=published_dir or PUBLISHED_DIR,
                     editorial_dir=editorial_dir,
                     preview_issue=(preview_issues or {}).get(league.slug))
    _write(out, "index.html",
           env.get_template("public/root.html").render(site_url=SITE_URL), pages)
    from leaguepage.desk_site import read_about

    _write(out, "about/index.html",
           env.get_template("public/about.html").render(
               site_url=SITE_URL, support_url=SUPPORT_URL,
               support_label=SUPPORT_LABEL,
               about_html=_render_md(read_about())), pages)
    # One stylesheet per theme, written once and cached, instead of the whole
    # thing inlined into all 98 documents. It was 728KB of the build's 1.9MB
    # of HTML, and 74% of the bytes on the smallest pages, re-sent on every
    # click through a fifty-five issue archive.
    assets_dir = out / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    css_tpl = env.get_template("public/_site_css.html")
    for league in leagues or LEAGUES:
        (assets_dir / f"{league.slug}.css").write_text(
            css_tpl.render(league=league), encoding="utf-8")
        pages.append(f"assets/{league.slug}.css")
    # The league-select page does not extend base.html and carries its own
    # twenty-five lines; a third stylesheet for one page nothing else shares
    # would cost a request to save a kilobyte.
    if STATIC_DIR.is_dir():
        assets = out / "assets"
        assets.mkdir(parents=True, exist_ok=True)
        # Copy only what the built pages actually ask for, and only in the
        # shapes a public site has any use for. Copying the directory
        # wholesale shipped a 176KB logo no page references, and would ship
        # a roster export or a stray database dropped in here with no
        # inspection at all: the audit skips file types it cannot read.
        rendered = "\n".join(p.read_text(encoding="utf-8", errors="ignore")
                             for p in out.rglob("*.html"))
        for f in sorted(STATIC_DIR.iterdir()):
            if not f.is_file():
                continue
            if f.suffix.lower() not in PUBLISHABLE_ASSETS:
                warnings.append(f"static/{f.name}: not a publishable asset type; skipped")
                continue
            if f"assets/{f.name}" not in rendered:
                warnings.append(f"static/{f.name}: referenced by no page; not deployed")
                continue
            shutil.copy2(f, assets / f.name)
    # The audit needs these to tell a manager's private alias from the
    # nickname he put in his own team name.
    return {"out_dir": out, "pages": pages, "warnings": warnings,
            "public_names": sorted(published_names)}


# ------------------------------------------------------------------ audit

# The only file shapes a static public site has a reason to serve. Anything
# else in static/ is either a mistake or a private file in the wrong place.
PUBLISHABLE_ASSETS = (".js", ".css", ".png", ".jpg", ".jpeg", ".svg", ".webp",
                      ".woff2", ".ico")

AUDITED_SUFFIXES = (".html", ".htm", ".js", ".json", ".txt", ".xml", ".css", ".md", ".svg")

# A stylesheet keyword is not a person, so the name scan reads what a
# reader can read. Two alternatives rather than one backreference,
# because the pattern is easier to check that way.
_SCRIPT_OR_STYLE_RE = re.compile(
    r"<style[^>]*>.*?</style[^>]*>|<script[^>]*>.*?</script[^>]*>",
    re.S | re.I)


def _private_handles(public_names: list[str] | None = None) -> list[str]:
    """Every name a manager is known by locally that he has not already
    published himself.

    Only Sleeper handles were read here, and aliases are precisely where
    real first names and nicknames live — so the strings most likely to
    identify somebody were the ones the audit could not see.

    The catch is that half of those aliases are *not* private: managers put
    their own nicknames in their team names, so "McLovin" is both an alias
    and part of "Statistical Anomalies (McLovin)". Scanning for aliases
    without subtracting the public names flagged 103 violations on a clean
    build. A name he published himself is his to publish, so anything
    appearing inside a current public team name is dropped.

    Aliases are therefore only scanned when the caller can say what the
    public names are. Without that list this returns handles alone, which is
    the behaviour that has always shipped.
    """
    from leaguepage.config import EDITORIAL_DIR

    path = EDITORIAL_DIR / "managers.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    is_published = published_matcher(list(public_names or []))
    out: set[str] = set()
    for key, m in data.items():
        if not isinstance(m, dict):
            continue
        candidates = [key, m.get("display_name")]
        if public_names is not None:
            for field in ("aliases", "unverified_aliases"):
                v = m.get(field)
                if isinstance(v, list):
                    candidates += [x for x in v if isinstance(x, str)]
                elif isinstance(v, str):
                    candidates.append(v)
        for c in candidates:
            if c and len(c) >= MIN_HANDLE_LEN and not is_published(c):
                out.add(c)
    return sorted(out)


def audit_output(out_dir: Path, *, extra_forbidden: list[str] | None = None,
                 public_names: list[str] | None = None) -> list[str]:
    """Scan the built site for private material. Historical archive pages are
    exempt from the handle scan only (published newsletters are verbatim);
    every other check applies everywhere."""
    violations = []
    handles = _private_handles(public_names) + (extra_forbidden or [])
    for path in sorted(out_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in AUDITED_SUFFIXES:
            continue
        rel = path.relative_to(out_dir).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for marker in ALWAYS_FORBIDDEN:
            if marker in text:
                violations.append(f"{rel}: forbidden marker '{marker}'")
        for pattern, label in PRIVATE_PATTERNS:
            m = pattern.search(text)
            if m:
                # The finding names the class and where it is, never the
                # value: an audit report that quotes the secret has
                # published it again.
                violations.append(f"{rel}: {label} at offset {m.start()}")
        # Now that the stylesheet is a real file, the whole of it would be
        # scanned as prose, and a stylesheet is where `border:3px double`
        # lives. Names are looked for where a reader reads them. Credential
        # shapes above still scan every audited file including this one.
        if "/archive/a" not in f"/{rel}" and path.suffix.lower() != ".css":
            # Names are looked for in what a reader can read. A stylesheet
            # keyword is not a person: matching case-insensitively made
            # `border:3px double` a hit for a manager whose alias is
            # "Double". Credential shapes above still scan everything,
            # because a token inside a script really is published.
            prose = _SCRIPT_OR_STYLE_RE.sub(" ", text)
            for h in handles:
                # Case-insensitive and at word boundaries. Plain `h in text`
                # missed a lowercased mention of a private alias, and hit
                # inside longer words, so it was simultaneously too loose
                # and too tight.
                if handle_re(h).search(prose):
                    violations.append(f"{rel}: private handle '{h}'")
    return violations
