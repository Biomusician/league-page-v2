"""Publishing skeleton: approved Markdown -> static public issue page.

Lifecycle (tracked in the issues table): generated -> edited -> approved ->
published. The chain of custody:

  1. Claude Code writes editorial/<season>/<league>/<issue>/draft-issue.md,
     which MUST begin with the ROUGH DRAFT marker (status: generated).
  2. Jonathan edits and saves his version as issue.md in the same directory
     (status: edited). The marker must be gone from issue.md.
  3. Approval is an explicit act (Desk/CLI) — status: approved.
  4. publish_issue() renders issue.md to site/<league>/<season>/<key>.html.

Generated prose can never reach the public site directly: publish refuses
anything that isn't approved, and refuses any source still carrying the
ROUGH DRAFT marker.
"""
from __future__ import annotations

from pathlib import Path

import markdown
from jinja2 import Environment, FileSystemLoader

from leaguepage.config import EDITORIAL_DIR, SITE_DIR, TEMPLATES_DIR, League
from leaguepage.storage import Storage

import re as _re

ROUGH_DRAFT_MARKER = "ROUGH DRAFT - COMMISSIONER EDIT REQUIRED"
# Any of these in source text marks material that must never publish.
BLOCKED_MARKERS = (ROUGH_DRAFT_MARKER, "TEST DRAFT", "provisional label")

_HTML_COMMENT_RE = _re.compile(r"<!--.*?-->", _re.DOTALL)


def strip_editorial_comments(text: str) -> str:
    """HTML comments carry commissioner-only metadata (usage trackers, naming
    notes); they never reach published page source."""
    return _HTML_COMMENT_RE.sub("", text)


class PublishError(RuntimeError):
    pass


def issue_source_path(league: League, season: str, issue_key: str) -> Path:
    return EDITORIAL_DIR / season / league.slug / issue_key / "issue.md"


def mark_edited(storage: Storage, league: League, season: str, issue_key: str) -> Path:
    src = issue_source_path(league, season, issue_key)
    if not src.exists():
        raise PublishError(
            f"{src} not found — save your edited version of draft-issue.md as issue.md first."
        )
    if ROUGH_DRAFT_MARKER in src.read_text(encoding="utf-8"):
        raise PublishError(
            "issue.md still contains the ROUGH DRAFT marker — remove it once your edit is done."
        )
    storage.set_issue_status(
        league_slug=league.slug, season=season, issue_key=issue_key,
        status="edited", source_path=src.as_posix(),
    )
    return src


def approve(storage: Storage, league: League, season: str, issue_key: str) -> None:
    mark_edited(storage, league, season, issue_key)  # re-validates the source
    storage.set_issue_status(
        league_slug=league.slug, season=season, issue_key=issue_key, status="approved",
    )


def publish_issue(
    storage: Storage,
    league: League,
    season: str,
    issue_key: str,
    *,
    site_dir: Path | None = None,
) -> Path:
    issue = storage.get_issue(league.slug, season, issue_key)
    if not issue or issue["status"] not in ("approved", "published"):
        raise PublishError(
            f"Issue is '{issue['status'] if issue else 'not started'}' — approve it before publishing."
        )
    src = issue_source_path(league, season, issue_key)
    text = src.read_text(encoding="utf-8")
    if ROUGH_DRAFT_MARKER in text:
        raise PublishError("Refusing to publish: source still carries the ROUGH DRAFT marker.")

    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=True)
    template = env.get_template("public/issue.html")
    body_html = markdown.markdown(text, extensions=["tables", "smarty"])
    html = template.render(league=league, season=season, issue_key=issue_key, body=body_html)

    out = (site_dir or SITE_DIR) / league.slug / season / f"{issue_key}.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    storage.set_issue_status(
        league_slug=league.slug, season=season, issue_key=issue_key,
        status="published", published_path=out.as_posix(),
    )
    return out


def publish_assembled_issue(
    storage: Storage,
    league: League,
    season: str,
    issue_key: str,
    *,
    published_dir: Path | None = None,
    base_dir: Path | None = None,
    week: int | None = None,
) -> Path:
    """Publish a fully assembled issue: enforce the gates (every included
    module approved, no blocked markers, all public team names resolved),
    then FREEZE a snapshot under published/. The public site renders issues
    from snapshots only, so later editorial-file changes never mutate an
    already-published issue; running this again deliberately republishes."""
    import json as _json

    from leaguepage.config import PUBLISHED_DIR
    from leaguepage.issue_builder import assemble_issue
    from leaguepage.storage import utcnow_iso

    try:
        assembled = assemble_issue(storage, league, season, issue_key,
                                   base_dir=base_dir, week=week, enforce=True)
    except ValueError as exc:
        raise PublishError(str(exc)) from exc

    sections = []
    for s in assembled["sections"]:
        if s["kind"] == "auto" or not s.get("content_md"):
            continue
        sections.append({
            "module_key": s["module_key"],
            "title": s["title"],
            "content_md": strip_editorial_comments(s["content_md"]).strip(),
            "credit": "by the Commissioner" if s["module_key"] == "lowdown" else None,
        })
    snapshot = {
        "league": league.slug, "season": season, "issue_key": issue_key,
        "issue_label": issue_key.replace("week-", "Week ").replace("draft", "Draft Issue"),
        "published_at": utcnow_iso(),
        "sections": sections,
    }
    snap_path = ((published_dir or PUBLISHED_DIR) / league.slug / season
                 / f"{issue_key}.json")
    snap_path.parent.mkdir(parents=True, exist_ok=True)
    snap_path.write_text(_json.dumps(snapshot, indent=1, ensure_ascii=False) + "\n",
                         encoding="utf-8")
    storage.set_issue_status(league_slug=league.slug, season=season, issue_key=issue_key,
                             status="published", published_path=snap_path.as_posix())
    return snap_path


def render_league_home(storage: Storage, league: League, *, site_dir: Path | None = None) -> Path:
    """League front page: latest published issue, archive of published issues,
    standings, teams. Only published content and public names appear."""
    from leaguepage.team_names import resolve_public_names

    league_data = storage.get_league(league.league_id) or {}
    season = str(league_data.get("season") or "")
    names = resolve_public_names(storage, league)
    rosters = storage.get_rosters(league.league_id)
    standings = []
    for r in sorted(rosters, key=lambda r: (-(r.get("settings") or {}).get("wins", 0),
                                            -float((r.get("settings") or {}).get("fpts", 0)))):
        s = r.get("settings") or {}
        nm = names.get(r["roster_id"], {}).get("name")
        standings.append({
            "name": nm or f"Roster {r['roster_id']}",
            "wins": s.get("wins", 0), "losses": s.get("losses", 0),
            "fpts": round(float(s.get("fpts", 0)) + float(s.get("fpts_decimal", 0)) / 100, 1),
        })
    published = [
        dict(row) for row in storage._conn.execute(
            "SELECT * FROM issues WHERE league_slug=? AND status='published' "
            "ORDER BY season DESC, issue_key DESC", (league.slug,)).fetchall()
    ]
    for p in published:
        p["href"] = f"{p['season']}/{p['issue_key']}.html"
        p["label"] = p["issue_key"].replace("week-", "Week ").replace("draft", "Draft Issue")
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=True)
    html = env.get_template("public/league_home.html").render(
        league=league, season=season, standings=standings, published=published,
        latest=published[0] if published else None,
    )
    out = (site_dir or SITE_DIR) / league.slug / "index.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out


def render_week(
    storage: Storage,
    league: League,
    week: int,
    *,
    site_dir: Path | None = None,
    editorial_dir: Path | None = None,
) -> Path | None:
    """Public Common Tactical Picture page for one week.

    Only APPROVED or LOCKED matchup drafts render; anything else shows
    'Preview pending' — generated text can never reach the public site
    before commissioner approval. Returns None if the week has no matchups."""
    from leaguepage.matchup_packet import (
        ROUGH_DRAFT_MARKER as MARKER, compute_week, matchup_status, week_dir,
    )

    from leaguepage.team_names import require_public_names

    computed = compute_week(storage, league, week)
    if computed is None:
        return None
    season = computed["analysis"]["season"]
    root = week_dir(league, season, week, editorial_dir)

    involved = [t["roster_id"] for sm in computed["scored"] for t in sm["matchup"]["teams"]]
    try:
        public_names = require_public_names(storage, league, involved)
    except ValueError as exc:
        raise PublishError(str(exc)) from exc

    cards = []
    for sm in computed["scored"]:
        m = sm["matchup"]
        slug = m["matchup_slug"]
        draft_path = root / "matchups" / slug / "draft.md"
        text = draft_path.read_text(encoding="utf-8") if draft_path.exists() else ""
        status = matchup_status(sm["state"], bool(text))
        approved = (status in ("approved", "locked") and text
                    and not any(b in text for b in BLOCKED_MARKERS))
        for t in m["teams"]:
            t["public_name"] = public_names[t["roster_id"]]
        cards.append({
            "matchup": m,
            "tags": sm["tags"],
            "prominence": (sm["state"] or {}).get("prominence_override") or sm["recommended_prominence"],
            "preview_html": markdown.markdown(strip_editorial_comments(text),
                                              extensions=["smarty"]) if approved else None,
        })
    cards.sort(key=lambda c: ("FEATURE", "MAJOR", "STANDARD", "CAPSULE").index(c["prominence"]))

    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=True)
    template = env.get_template("public/week.html")
    html = template.render(league=league, season=season, week=week, cards=cards)
    out = (site_dir or SITE_DIR) / league.slug / season / f"week-{week:02d}.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out
