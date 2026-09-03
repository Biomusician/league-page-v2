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

import markdown as md
from jinja2 import Environment, FileSystemLoader

from leaguepage.config import DIST_DIR, LEAGUES, PUBLISHED_DIR, STATIC_DIR, TEMPLATES_DIR, League
from leaguepage.editorial import load_coalitions
from leaguepage.matchup_analysis import all_play, analyze_week, team_record, weekly_scores
from leaguepage.matchup_packet import compute_week, matchup_status, week_dir
from leaguepage.publish import BLOCKED_MARKERS, strip_editorial_comments
from leaguepage.storage import Storage
from leaguepage.team_names import resolve_public_names

FLAGS = {"France": "\U0001F1EB\U0001F1F7", "United Kingdom": "\U0001F1EC\U0001F1E7",
         "Japan": "\U0001F1EF\U0001F1F5", "Sweden": "\U0001F1F8\U0001F1EA"}

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
    return md.markdown(strip_editorial_comments(text), extensions=["tables", "smarty"])


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
    rows_out = []
    for r in sorted(rosters, key=lambda r: (-(r.get("settings") or {}).get("wins", 0),
                                            -team_record(r)["fpts"])):
        rid = r["roster_id"]
        rec = team_record(r)
        apd = ap.get(rid)
        at = a_teams.get(rid, {})
        eff = None
        rows_out.append({
            "roster_id": rid,
            "name": names[rid]["name"] or f"Roster {rid}",
            "wins": rec["wins"], "losses": rec["losses"],
            "pf": rec["fpts"], "pa": round(pa.get(rid, 0.0), 1),
            "streak": at.get("streak"),
            "all_play": f"{apd['wins']}-{apd['losses']}" if apd else None,
            "points_rank": fpts_rank[rid],
            "efficiency": eff,
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

    def render(rel: str, template: str, depth: int, **ctx) -> None:
        # lroot is the prefix from this page back to the LEAGUE root. Derive it
        # from the output path itself; the historical depth argument counted
        # from the site root and left every relative link one level too high.
        lroot = "../" * rel.count("/")
        html = env.get_template(template).render(
            league=league, season=season, lroot=lroot, **ctx)
        _write(out_dir, f"{league.slug}/{rel}", html, pages)

    # issue permalinks from frozen snapshots
    latest_ctx = None
    for snap in snaps:
        ctx = _issue_ctx(snap)
        if latest_ctx is None:
            latest_ctx = ctx
        render(f"{snap['season']}/{snap['issue_key']}/index.html", "public/issue_page.html",
               2, issue=ctx, current_nav=None,
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
               2, issue=ctx, current_nav=None)
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
                "anchor": m["matchup_slug"], "names": names_line, "records": rec,
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
    render("standings/index.html", "public/standings.html", 2,
           standings=standings, weeks_played=weeks_played,
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
                "outcome": row.get("outcome")}

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
            _picks = [dict(p, dv=classify_pick(p.get("delta"), league_size))
                      for p in t["picks_by_round"]]
            # Headline Reach/Steal are skill positions only, matching the
            # Draft page: overall ECR ranks every K and DST below the
            # draftable range while lineups force everyone to draft one, so
            # a kicker's 80-pick "reach" measures the reference board's
            # shape, not a roster decision. It was headlining team pages as
            # the Biggest Reach, which is the calibration decision leaking.
            _hd = headline_deviations(t["picks_by_round"], league_size, top=1)
            _st = [dict(p, dv=classify_pick(p["delta"], league_size),
                        context=position_order_context(_adp, analysis_picks, p))
                   for p in _hd["special_teams"][:2]]
            recap_by_rid[t["roster_id"]] = {
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

    moves_ctx_by_rid = {rid: [_move_ctx(r) for r in rows]
                        for rid, rows in tx_by_rid.items()}
    public_names = {rid: v["name"] or f"Roster {rid}" for rid, v in names.items()}
    if computed:
        handles = _private_handles()
        by_anchor = {sm["matchup"]["matchup_slug"]: sm for sm in computed["scored"]}
        for card in cards:
            sm = by_anchor.get(card["anchor"])
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
            card["past_statement"] = receipts_for_matchup(
                storage, league, season, week, names,
                [t["roster_id"] for t in sm["matchup"]["teams"]]) if sm else None
    render("matchups/index.html", "public/matchups.html", 2,
           cards=cards, week=week, current_nav="Common Tactical Picture")

    # Peer and Near-Peer: the Commissioner's ranking is authoritative when it
    # exists; the Model Board fills the page when it does not, and stays on
    # as a comparison column when it does, because the disagreement is the
    # entertaining part.
    from leaguepage.model_views import compare_to_commissioner, model_board

    board = model_board(profile=profile,
                        names={rid: v["name"] or f"Roster {rid}"
                               for rid, v in names.items()},
                        slugs=slugs, standings=standings, form=form,
                        weeks_played=weeks_played_league)
    if ranking_ctx:
        ranking_ctx = compare_to_commissioner(board, ranking_ctx)
    render("power/index.html", "public/power.html", 2,
           rankings=ranking_ctx, label=label, board=board,
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

    team_cards = []
    for r in storage.get_rosters(league.league_id):
        rid = r["roster_id"]
        rec = team_record(r)
        nm = names[rid]["name"] or f"Roster {rid}"
        st = next((i + 1 for i, s in enumerate(standings) if s["roster_id"] == rid), None)
        team_cards.append({"slug": slugs[rid], "name": nm,
                           "record": f"{rec['wins']}-{rec['losses']}", "standing": st,
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
        e_str, e_weak = editorial_strengths(profile, rid, sw)
        model_rank = next((row["rank"] for row in board["rows"]
                           if row["roster_id"] == rid), None)
        # exact side match, never a substring: "Dave" lives inside plenty of
        # other strings
        next_card = next(({"names": c["names"], "anchor": c["anchor"],
                           "note": (", ".join(c["tags"]) if c["tags"] else
                                    ("the result is on the board" if c["score"]
                                     else f"week {week} on the board"))}
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
        render(f"team/{slugs[rid]}/index.html", "public/team.html", 3,
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
                     "stage": profile["stage"]},
               current_nav="Teams")
    render("teams/index.html", "public/teams.html", 2,
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
    render("transactions/index.html", "public/transactions.html", 2,
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
                          "dv": classify_pick(p["delta"], league_size)})
        for t in analysis["teams"]:
            team_sections.append({
                "slug": t["team_slug"], "name": public_of[t["team_slug"]],
                "position_counts": ", ".join(f"{n} {pos}" for pos, n in t["position_counts"].items()),
                "picks": recap_by_rid.get(t["roster_id"], {}).get("picks", []),
            })

        def _headline_ctx(p: dict, *, st_context: bool = False) -> dict:
            from leaguepage.draft_value import position_order_context

            return {"name": p["name"], "pick_no": p["pick_no"], "adp": p["adp"],
                    "team": public_of.get(p["team_slug"], p["team_slug"]),
                    "team_slug": p["team_slug"],
                    "dv": classify_pick(p["delta"], league_size),
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
            {"label": "Highest single-week score", "holder": names[hi[0]]["name"] or f"Roster {hi[0]}",
             "value": f"{hi[2]:g}", "when": f"week {hi[1]}"},
            {"label": "Lowest single-week score", "holder": names[lo[0]]["name"] or f"Roster {lo[0]}",
             "value": f"{lo[2]:g}", "when": f"week {lo[1]}"},
        ]
    from leaguepage.model_views import black_box_preview

    render("black-box/index.html", "public/blackbox.html", 2,
           records=records, population=population,
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
        seasons = sorted(by_season.items(), key=lambda kv: kv[0], reverse=True)
        historical_groups.append({"title": title, "seasons": seasons})
        for it in items:
            full = storage.get_archive_issue(it["issue_id"]) or {}
            paragraphs = [p.strip() for p in re.split(r"\n\s*\n", full.get("body") or "")
                          if p.strip()]
            origin = "Disco Chat" if slug_key == "disco" else "Big Daddy AF"
            render(f"archive/a{it['issue_id']}/index.html", "public/archive_issue.html", 3,
                   item={"title": it["title"], "season": it["season"], "week": it["week"],
                         "origin": origin, "paragraphs": paragraphs},
                   current_nav="Archive")
    render("archive/index.html", "public/archive.html", 2,
           published=snaps, historical_groups=historical_groups, current_nav="Archive")

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
    render("index.html", "public/home.html", 1,
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
    for league in (leagues or LEAGUES):
        build_league(storage, league, env, out, pages, warnings,
                     published_dir=published_dir or PUBLISHED_DIR,
                     editorial_dir=editorial_dir,
                     preview_issue=(preview_issues or {}).get(league.slug))
    _write(out, "index.html", env.get_template("public/root.html").render(), pages)
    if STATIC_DIR.is_dir():
        assets = out / "assets"
        assets.mkdir(parents=True, exist_ok=True)
        for f in STATIC_DIR.iterdir():
            if f.is_file():
                shutil.copy2(f, assets / f.name)
    return {"out_dir": out, "pages": pages, "warnings": warnings}


# ------------------------------------------------------------------ audit

ALWAYS_FORBIDDEN = [
    "ROUGH DRAFT - COMMISSIONER EDIT REQUIRED", "TEST DRAFT", "provisional label",
    "sleeper:pick:", "sleeper:roster:", "sleeper:matchup:", "sleeper:transaction:",
    "editorial:manager:", "editorial:coalition:", "computed:", "archive:issue:",
    "AUTHORING", "commissioner_notes", "REVIEW_PACKET",
    "C:/Users", "C:\\Users", "League-Page-PRIVATE",
]


def _private_handles() -> list[str]:
    """Sleeper handles from local managers.json — never publishable outside
    the verbatim historical archive."""
    from leaguepage.config import EDITORIAL_DIR

    path = EDITORIAL_DIR / "managers.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for key, m in data.items():
        if not isinstance(m, dict):
            continue
        dn = m.get("display_name")
        if dn and len(dn) >= 4:
            out.append(dn)
    return out


def audit_output(out_dir: Path, *, extra_forbidden: list[str] | None = None) -> list[str]:
    """Scan the built site for private material. Historical archive pages are
    exempt from the handle scan only (published newsletters are verbatim);
    every other check applies everywhere."""
    violations = []
    handles = _private_handles() + (extra_forbidden or [])
    for path in out_dir.rglob("*.html"):
        rel = path.relative_to(out_dir).as_posix()
        text = path.read_text(encoding="utf-8")
        for marker in ALWAYS_FORBIDDEN:
            if marker in text:
                violations.append(f"{rel}: forbidden marker '{marker}'")
        if "/archive/a" not in f"/{rel}":
            for h in handles:
                if h in text:
                    violations.append(f"{rel}: private handle '{h}'")
    return violations
