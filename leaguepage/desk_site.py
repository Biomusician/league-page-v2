"""Desk surfaces that belong to the site rather than to a week.

Two things live here, and what they have in common is that neither is
weekly editorial work: the About page's copy, and the Force Flow review
queue. Keeping them out of `desk_editor` is the point -- nothing in this
module can add a publication blocker, appear in a readiness count, or make
an issue look unfinished.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import Form, Request
from fastapi.responses import JSONResponse, RedirectResponse

from leaguepage import prose
from leaguepage.config import EDITORIAL_DIR, LEAGUES, SEASON, get_league

# Markdown on disk, beside the rest of the editorial source, so it diffs,
# it is in the backup bundle, and it needs no schema of its own.
ABOUT_PATH = EDITORIAL_DIR / "site" / "about.md"

DEFAULT_ABOUT = """# About League Page

Information about the project will be added here.
"""


def read_about(path: Path | None = None) -> str:
    p = path or ABOUT_PATH
    return p.read_text(encoding="utf-8") if p.exists() else DEFAULT_ABOUT


def write_about(text: str, path: Path | None = None) -> Path:
    p = path or ABOUT_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def register_site(app, storage, templates) -> None:
    @app.get("/commissioner/site/about")
    def about_editor(request: Request):
        return templates.TemplateResponse(request, "desk/about.html", {
            "text": read_about(),
            "preview": prose.render(read_about()),
            "path": ABOUT_PATH.as_posix(),
        })

    @app.post("/commissioner/site/about")
    def about_save(text: str = Form(""), action: str = Form("save")):
        if action == "save":
            write_about(text)
        return RedirectResponse("/commissioner/site/about", status_code=303)

    @app.post("/commissioner/site/about/preview")
    async def about_preview(request: Request):
        body = await request.json()
        return JSONResponse({"ok": True,
                             "html": prose.render(str(body.get("text") or ""))})

    # ------------------------------------------------------- force flow

    @app.get("/commissioner/{league_slug}/{season}/force-flow")
    def force_flow_review(request: Request, league_slug: str, season: str):
        from leaguepage.force_flow import review

        league = get_league(league_slug)
        with storage() as s:
            week = int(s.get_meta("current_week") or 1)
            rows = review(s, league, season, week)
        return templates.TemplateResponse(request, "desk/force_flow.html", {
            "league": league, "season": season, "week": week, "rows": rows,
            "leagues": LEAGUES,
        })

    @app.post("/commissioner/{league_slug}/{season}/force-flow/note")
    def force_flow_note(league_slug: str, season: str,
                        txn_id: str = Form(...), note: str = Form("")):
        """A blurb is optional everywhere. Saving an empty one removes it,
        which is how he takes a note back without a second control."""
        with storage() as s:
            s.set_force_flow_note(league_slug=league_slug, season=season,
                                  txn_id=txn_id, note=note)
        return RedirectResponse(
            f"/commissioner/{league_slug}/{season}/force-flow#txn-{txn_id}",
            status_code=303)
