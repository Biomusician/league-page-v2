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

from leaguepage.config import DIST_DIR, LEAGUES, PUBLISHED_DIR, TEMPLATES_DIR, League
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
    root = published_dir / league.slug
    snaps = []
    if root.exists():
        for p in sorted(root.rglob("*.json"), reverse=True):
            snap = json.loads(p.read_text(encoding="utf-8"))
            snap["href"] = f"{snap['season']}/{snap['issue_key']}/index.html"
            snaps.append(snap)
    # newest first: season desc, weekly issues over draft, week number desc
    def _key(s):
        wk = s["issue_key"]
        return (s["season"], 1 if wk.startswith("week-") else 0, wk)
    snaps.sort(key=_key, reverse=True)
    return snaps


def _issue_ctx(snap: dict, *, preview: bool = False) -> dict:
    sections = []
    for s in snap["sections"]:
        sections.append({
            "anchor": s["module_key"],
            "title": s["title"],
            "credit": s.get("credit"),
            "html": _render_md(s["content_md"]),
        })
    lowdown = next((s for s in snap["sections"] if s["module_key"] == "lowdown"), None)
    excerpt = None
    if lowdown:
        paras = [p for p in lowdown["content_md"].split("\n\n")
                 if p.strip() and not p.strip().startswith("#")]
        excerpt = _render_md("\n\n".join(paras[:2]))
    return {
        "issue_key": snap["issue_key"], "issue_label": snap["issue_label"],
        "season": snap["season"], "published_at": snap.get("published_at"),
        "preview": preview, "sections": sections,
        "section_titles": [{"anchor": s["anchor"], "title": s["title"]} for s in sections],
        "lowdown_excerpt_html": excerpt,
        "href": snap["href"],
    }


def _standings_rows(storage: Storage, league: League, names: dict[int, dict],
                    week: int) -> tuple[list[dict], int]:
    rosters = storage.get_rosters(league.league_id)
    scores = weekly_scores(storage, league.league_id, week)
    ap = all_play(scores)
    weeks_played = len({wk for rows in scores.values() for wk, _ in rows})
    analysis = analyze_week(storage, league, week) if weeks_played else None
    a_teams = {t["roster_id"]: t for t in (analysis or {}).get("teams", {}).values()} if analysis else {}
    pa: dict[int, float] = defaultdict(float)
    for wk in range(1, week + 1):
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
        lroot = "../" * depth
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
               2, issue=ctx, current_nav=None)

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
    render("matchups/index.html", "public/matchups.html", 2,
           cards=cards, week=week, current_nav="Common Tactical Picture")

    # standings
    standings, weeks_played = _standings_rows(storage, league, names, week)
    render("standings/index.html", "public/standings.html", 2,
           standings=standings, weeks_played=weeks_played, current_nav="Standings")

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
                    })
                break
    render("power/index.html", "public/power.html", 2,
           rankings=ranking_ctx, label=label, current_nav="Peer and Near-Peer")

    # teams index + team pages
    published_awards = []
    for snap in snaps:
        d = storage.get_award_decisions(league.slug, snap["season"], snap["issue_key"])
        for key, dec in d.items():
            if dec["decision"] in ("awarded", "manual"):
                published_awards.append({"award_key": key, "winner": dec.get("winner"),
                                         "issue": f"{snap['season']} {snap['issue_label']}"})
    scores = weekly_scores(storage, league.league_id, week)
    ap = all_play(scores)
    team_cards = []
    for r in storage.get_rosters(league.league_id):
        rid = r["roster_id"]
        rec = team_record(r)
        nm = names[rid]["name"] or f"Roster {rid}"
        st = next((i + 1 for i, s in enumerate(standings) if s["roster_id"] == rid), None)
        team_cards.append({"slug": slugs[rid], "name": nm,
                           "record": f"{rec['wins']}-{rec['losses']}", "standing": st,
                           "coalition": _coalition_card(coalitions, league, rid)})
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
        mentions = [{"href": s["href"], "label": f"{s['season']} {s['issue_label']}"}
                    for s in snaps
                    if any(nm in sec["content_md"] for sec in s["sections"])]
        render(f"team/{slugs[rid]}/index.html", "public/team.html", 3,
               team={"name": nm, "record": f"{rec['wins']}-{rec['losses']}",
                     "standing": st, "pf": rec["fpts"],
                     "co_managed": bool(r.get("co_owners")),
                     "coalition": _coalition_card(coalitions, league, rid),
                     "score_history": scores.get(rid, []),
                     "all_play": f"{apd['wins']}-{apd['losses']}" if apd else None,
                     "awards": awards, "roster": roster_players,
                     "mentions": mentions},
               current_nav="Teams")
    render("teams/index.html", "public/teams.html", 2,
           teams=team_cards, current_nav="Teams")

    # transactions (Force Flow)
    log = []
    for wk in range(1, week + 1):
        for t in storage.get_transactions(league.league_id, wk):
            if t.get("status") != "complete":
                continue
            def _names_for(mapping):
                return ", ".join((storage.get_player(pid) or {}).get("full_name") or pid
                                 for pid in (mapping or {}))
            rids = set((t.get("adds") or {}).values()) | set((t.get("drops") or {}).values())
            log.append({
                "week": wk, "type": (t.get("type") or "?").replace("_", " "),
                "team": ", ".join(sorted(names[rid]["name"] or f"Roster {rid}" for rid in rids)),
                "adds": _names_for(t.get("adds")), "drops": _names_for(t.get("drops")),
                "faab": sum(x.get("amount", 0) for x in (t.get("waiver_budget") or [])) or None,
            })
    render("transactions/index.html", "public/transactions.html", 2,
           log=log, editorial_sections=_published_module_sections(snaps, "forceflow"),
           current_nav="Force Flow")

    # draft page
    from leaguepage.adp import load_adp_for_league
    from leaguepage.draft_analysis import analyze_league_draft

    analysis = analyze_league_draft(storage, league, managers={}, adp=load_adp_for_league(league))
    board, team_sections, status_line, prov = [], [], "", None
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
                          "team": public_of.get(p["team_slug"], p["team_slug"])})
        for t in analysis["teams"]:
            team_sections.append({
                "slug": t["team_slug"], "name": public_of[t["team_slug"]],
                "position_counts": ", ".join(f"{n} {pos}" for pos, n in t["position_counts"].items()),
                "picks": t["picks_by_round"],
            })
    recap = next((s for s in snaps if s["issue_key"] == "draft"), None)
    render("draft/index.html", "public/draft.html", 2,
           board=board, team_sections=team_sections, status_line=status_line,
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
    render("black-box/index.html", "public/blackbox.html", 2,
           records=records, population=population,
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

    # league home (front page)
    feature = next((c for c in cards if c["prominence"] == "FEATURE" and (c["preview_html"] or c["tags"])), None)
    render("index.html", "public/home.html", 1,
           latest=latest_ctx, standings=standings, published=snaps,
           feature_matchup=feature, current_nav="Home")


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
        shutil.rmtree(out)
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
