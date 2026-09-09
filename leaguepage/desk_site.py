"""Desk surfaces that belong to the site rather than to a week.

Two things live here, and what they have in common is that neither is
weekly editorial work: the About page's copy, and the Force Flow review
queue. Keeping them out of `desk_editor` is the point -- nothing in this
module can add a publication blocker, appear in a readiness count, or make
an issue look unfinished.
"""
from __future__ import annotations

from fastapi import Form, Request
from fastapi.responses import JSONResponse, RedirectResponse

from leaguepage import (auth, editorial_store, prose, prose_store,
                        site_documents)
from leaguepage.config import LEAGUES, SEASON, get_league

# About's copy and its storage moved to `site_documents`, because the WRITE
# has to go through the store like every other authoring route. `read_about`
# stays here because `site_build` imports it; `DEFAULT_ABOUT` stays as a
# re-export for the tests that assert the shipped text.
#
# There is deliberately no ABOUT_PATH any more: on Postgres there is no
# path, and a module constant computed at import time would have frozen the
# filesystem one anyway. `site_documents.path_for()` answers when a path is
# what is wanted.
DEFAULT_ABOUT = site_documents.DEFAULT_ABOUT


def read_about() -> str:
    """The About copy from whichever backend is authoritative, or the
    shipped default. No actor: the public build reads this too."""
    return site_documents.read(site_documents.ABOUT)


def _about_where() -> str:
    """What the editor tells him about where this is kept. A path when it is
    a path, the table name when it is not. Never a connection string."""
    if prose_store.backend_name() == prose_store.FILESYSTEM:
        return site_documents.path_for(site_documents.ABOUT).as_posix()
    return "site_documents / about (cloud)"


def register_site(app, storage, templates) -> None:
    _db_path: list = []

    def _editorial():
        """The one editorial store for this run. Same reasoning as the
        editor's: a route that writes should not also decide what a
        transaction is."""
        if not _db_path:
            with storage() as s:
                _db_path.append(s.db_path)
        return editorial_store.store(_db_path[0])

    @app.get("/commissioner/site/about")
    def about_editor(request: Request):
        body = read_about()
        return templates.TemplateResponse(request, "desk/about.html", {
            "text": body,
            "preview": prose.render(body),
            "where": _about_where(),
        })

    @app.post("/commissioner/site/about")
    def about_save(request: Request, text: str = Form(""),
                   action: str = Form("save")):
        """One store action, exactly like every other authoring route.

        On Postgres that makes the save one transaction as the signed-in
        Commissioner, so RLS applies to it; on the filesystem it is the
        same file write it always was.
        """
        if action == "save":
            with _editorial().action(actor=auth.actor_of(request)) as act:
                act.state.set_site_document(site_documents.ABOUT, text,
                                            act.actor)
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
    def force_flow_note(request: Request, league_slug: str, season: str,
                        txn_id: str = Form(...), note: str = Form("")):
        """A blurb is optional everywhere. Saving an empty one removes it,
        which is how he takes a note back without a second control."""
        with _editorial().action(actor=auth.actor_of(request)) as act:
            act.state.set_force_flow_note(league=league_slug, season=season,
                                          txn_id=txn_id, note=note)
        return RedirectResponse(
            f"/commissioner/{league_slug}/{season}/force-flow#txn-{txn_id}",
            status_code=303)
