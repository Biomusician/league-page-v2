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

from jinja2 import Environment, FileSystemLoader

from leaguepage import prose, provenance
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
    body_html = prose.render(text)
    html = template.render(league=league, season=season, issue_key=issue_key, body=body_html)

    out = (site_dir or SITE_DIR) / league.slug / season / f"{issue_key}.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    storage.set_issue_status(
        league_slug=league.slug, season=season, issue_key=issue_key,
        status="published", published_path=out.as_posix(),
    )
    return out


def _prose_only(sections: list[dict] | None) -> list[dict]:
    """Sections stripped of everything that is not the writing.

    Whether an issue has changed is a question about its prose. Provenance
    is a fact ABOUT that prose and lives in a gitignored database, so a
    fresh clone or a rebuilt DB would otherwise make an unchanged issue
    look changed -- refusing an identical republish with an error that says
    the text moved when it did not, and offering a correction whose only
    effect is to delete a true AI disclosure.
    """
    return [{k: v for k, v in s.items() if k != "provenance"}
            for s in (sections or [])]


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

    # Publication quality gate. Warnings are advisory by design (the
    # Commissioner overrides them by publishing); blockers are things no
    # reader should ever see and stop the pipeline here, before a snapshot
    # is frozen. Privacy blockers have no override path at all.
    from leaguepage import pubqa

    qa = pubqa.report(pubqa.check_sections(
        [s for s in assembled["sections"] if s.get("included", True)],
        pubqa.build_context(storage, league, season, issue_key, week=week)))
    if qa["blockers"]:
        raise PublishError(
            "Publication check failed — " + qa["headline"] + ": "
            + " | ".join(f"{b['category_label']}: {b['title']}"
                         + (f" ({b['excerpt'][:60]})" if b["excerpt"] and not b["privacy"] else "")
                         for b in qa["blockers"][:6]))

    sections = []
    for s in assembled["sections"]:
        if s["kind"] == "auto" or not s.get("content_md"):
            continue
        # Provenance is frozen with the content it describes. The claim
        # "nobody edited this" is only true of a particular text, so it
        # belongs in the snapshot beside that text rather than being looked
        # up later against a database that has moved on.
        prov = provenance.state_for(
            storage, league_slug=league.slug, season=season, issue_key=issue_key,
            section=s["module_key"], text=s["content_md"])
        sections.append({
            "module_key": s["module_key"],
            "title": s["title"],
            "content_md": strip_editorial_comments(s["content_md"]).strip(),
            "credit": "by the Commissioner" if s["module_key"] == "lowdown" else None,
            "provenance": prov,
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
    if snap_path.exists():
        # The whole promise of this directory is that what shipped that day
        # is still on disk. A republish used to overwrite it in place with
        # no revision and no "Updated" line, and the ordinary way to reach
        # that was a deploy that failed after the snapshot stage and got
        # retried. So: an identical re-entry is a no-op, and a changed one
        # is refused and pointed at the correction mechanism, which keeps
        # the original and adds a sibling.
        # ...measured against the latest revision: after a correction the
        # Desk text matches r2, not the original, and that is unchanged.
        latest = snapshot_family(snap_path.parent.parent.parent, league.slug, season,
                                 issue_key)[-1]
        prior = _json.loads(latest.read_text(encoding="utf-8"))
        if _prose_only(prior.get("sections")) == _prose_only(sections):
            storage.set_issue_status(league_slug=league.slug, season=season,
                                     issue_key=issue_key, status="published",
                                     published_path=snap_path.as_posix())
            return snap_path
        raise PublishError(
            f"{issue_key} was already published on "
            f"{(prior.get('published_at') or '')[:10]} and the text has changed "
            f"since. Publishing again would rewrite the record of what shipped. "
            f"Publish it as a correction instead: add a correction note on the "
            f"publish page (revise_issue) so the original is kept and the "
            f"change travels with a note.")
    snap_path.write_text(_json.dumps(snapshot, indent=1, ensure_ascii=False) + "\n",
                         encoding="utf-8")
    storage.set_issue_status(league_slug=league.slug, season=season, issue_key=issue_key,
                             status="published", published_path=snap_path.as_posix())
    return snap_path


# ------------------------------------------------------------- corrections

def text_changed_since_publish(
    storage: Storage,
    league: League,
    season: str,
    issue_key: str,
    *,
    published_dir: Path | None = None,
    base_dir: Path | None = None,
    week: int | None = None,
) -> bool | None:
    """Whether the prose on the Desk differs from the latest frozen
    revision. None when the issue has never been published. The same
    comparison publish_assembled_issue makes, so the publish page can say
    "a correction note is required" before a job is started and fails."""
    import json as _json

    from leaguepage.config import PUBLISHED_DIR
    from leaguepage.issue_builder import assemble_issue

    family = snapshot_family(published_dir or PUBLISHED_DIR, league.slug, season, issue_key)
    if not family:
        return None
    latest = _json.loads(family[-1].read_text(encoding="utf-8"))
    assembled = assemble_issue(storage, league, season, issue_key,
                               base_dir=base_dir, week=week, enforce=False)
    current = [{
        "module_key": s["module_key"], "title": s["title"],
        "content_md": strip_editorial_comments(s["content_md"]).strip(),
        "credit": "by the Commissioner" if s["module_key"] == "lowdown" else None,
    } for s in assembled["sections"]
        if s["kind"] != "auto" and s.get("content_md") and s.get("included", True)]
    return _prose_only(current) != _prose_only(latest.get("sections"))


REVISION_RE = _re.compile(r"^(?P<key>.+?)\.r(?P<n>\d+)$")


def snapshot_family(published_dir: Path, league_slug: str, season: str,
                    issue_key: str) -> list[Path]:
    """Every frozen file for one issue, oldest first: the original snapshot
    then each correction. Nothing here is ever rewritten or deleted."""
    root = published_dir / league_slug / season
    if not root.exists():
        return []
    out = [root / f"{issue_key}.json"] if (root / f"{issue_key}.json").exists() else []
    revs = []
    for p in root.glob(f"{issue_key}.r*.json"):
        m = REVISION_RE.match(p.stem)
        if m and m.group("key") == issue_key:
            revs.append((int(m.group("n")), p))
    return out + [p for _, p in sorted(revs)]


def revise_issue(
    storage: Storage,
    league: League,
    season: str,
    issue_key: str,
    *,
    note: str,
    sections: list[dict] | None = None,
    published_dir: Path | None = None,
    base_dir: Path | None = None,
    week: int | None = None,
) -> Path:
    """Publish a CORRECTION to an already-published issue.

    Published snapshots are immutable, and they stay that way: a correction
    is a NEW file (`<key>.r2.json`, then `.r3`, …) sitting beside the
    original, carrying what changed and why. The public site renders the
    latest revision and prints an 'Updated <date> · <note>' line; the
    original remains on disk and in git as the record of what was actually
    published on the day. Nothing about provenance is destroyed to fix a
    typo.

    `sections` defaults to re-assembling from the current editorial files,
    which is the normal path: the Commissioner fixes the prose, then issues
    a correction. The publication quality gate runs exactly as it does for a
    first publication."""
    import json as _json

    from leaguepage.config import PUBLISHED_DIR
    from leaguepage.storage import utcnow_iso

    if not note or not note.strip():
        raise PublishError("A correction needs a note saying what was corrected.")
    pdir = published_dir or PUBLISHED_DIR
    family = snapshot_family(pdir, league.slug, season, issue_key)
    if not family:
        raise PublishError(
            f"{league.slug} {season} {issue_key} has never been published; "
            "there is nothing to correct.")
    original = _json.loads(family[0].read_text(encoding="utf-8"))
    latest = _json.loads(family[-1].read_text(encoding="utf-8"))

    if sections is None:
        from leaguepage.issue_builder import assemble_issue

        try:
            assembled = assemble_issue(storage, league, season, issue_key,
                                       base_dir=base_dir, week=week, enforce=True)
        except ValueError as exc:
            raise PublishError(str(exc)) from exc
        # Same shape as publish_assembled_issue builds, provenance included.
        # It has to be: the "nothing changed" check below compares this
        # against the stored snapshot, and a section dict missing a key the
        # snapshot has can never compare equal, so an identical re-entry
        # would sail through as a correction.
        sections = [{
            "module_key": s["module_key"], "title": s["title"],
            "content_md": strip_editorial_comments(s["content_md"]).strip(),
            "credit": "by the Commissioner" if s["module_key"] == "lowdown" else None,
            "provenance": provenance.state_for(
                storage, league_slug=league.slug, season=season,
                issue_key=issue_key, section=s["module_key"],
                text=s["content_md"]),
        } for s in assembled["sections"] if s["kind"] != "auto" and s.get("content_md")]

    from leaguepage import pubqa

    qa = pubqa.report(pubqa.check_sections(
        sections, pubqa.build_context(storage, league, season, issue_key, week=week)))
    if qa["blockers"]:
        raise PublishError(
            "Correction refused — " + qa["headline"] + ": "
            + " | ".join(f"{b['category_label']}: {b['title']}"
                         for b in qa["blockers"][:6]))

    if _prose_only(sections) == _prose_only(latest.get("sections")):
        raise PublishError("The corrected text is identical to what is already "
                           "published; nothing to revise.")

    n = int(latest.get("revision") or 1) + 1
    snapshot = {
        **{k: v for k, v in original.items() if k != "sections"},
        "revision": n,
        "revises": issue_key,
        "original_published_at": original.get("published_at"),
        "revised_at": utcnow_iso(),
        "revision_note": note.strip(),
        "sections": sections,
    }
    path = pdir / league.slug / season / f"{issue_key}.r{n}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_json.dumps(snapshot, indent=1, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    return path


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
            "preview_html": prose.render(strip_editorial_comments(text)) if approved else None,
        })
    cards.sort(key=lambda c: ("FEATURE", "MAJOR", "STANDARD", "CAPSULE").index(c["prominence"]))

    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=True)
    template = env.get_template("public/week.html")
    html = template.render(league=league, season=season, week=week, cards=cards)
    out = (site_dir or SITE_DIR) / league.slug / season / f"week-{week:02d}.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out
