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

ROUGH_DRAFT_MARKER = "ROUGH DRAFT - COMMISSIONER EDIT REQUIRED"
# Any of these in source text marks material that must never publish.
BLOCKED_MARKERS = (ROUGH_DRAFT_MARKER, "TEST DRAFT", "provisional label")


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
            "preview_html": markdown.markdown(text, extensions=["smarty"]) if approved else None,
        })
    cards.sort(key=lambda c: ("FEATURE", "MAJOR", "STANDARD", "CAPSULE").index(c["prominence"]))

    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=True)
    template = env.get_template("public/week.html")
    html = template.render(league=league, season=season, week=week, cards=cards)
    out = (site_dir or SITE_DIR) / league.slug / season / f"week-{week:02d}.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out
