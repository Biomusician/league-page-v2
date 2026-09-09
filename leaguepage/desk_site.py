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

from leaguepage import auth, editorial_store, prose
from leaguepage.config import EDITORIAL_DIR, LEAGUES, SEASON, get_league

# Markdown on disk, beside the rest of the editorial source, so it diffs,
# it is in the backup bundle, and it needs no schema of its own.
ABOUT_PATH = EDITORIAL_DIR / "site" / "about.md"

# The shipped default, not the Commissioner's copy: he can replace all of
# it from the Desk at Site -> About, and then this is never read again.
#
# Written as the site's own methodology note rather than in his voice,
# because it is a disclosure about how the paper is made and not a piece of
# editorial writing. It says only what is already visible on the site --
# the six provenance labels the issue pages carry, where the numbers come
# from, and that published issues are immutable. Nothing here describes how
# any of it is built.
#
# One paragraph per list entry, unwrapped: `prose.render` honours a line
# break the way it does inside an issue, so wrapping this to 72 columns
# would publish the wraps as <br>.
_ABOUT_BLOCKS = [
    "# About League Page",

    "League Page is a weekly newspaper for the fantasy football leagues it"
    " covers. Each issue is written, edited and published by the league's"
    " Commissioner.",

    "## Who writes it",

    "The Commissioner has final say over everything published here. No"
    " section is published automatically, and nothing reaches a published"
    " issue without the Commissioner's approval.",

    "Some of the work behind an issue is assisted by AI: gathering"
    " research, summarising what has changed in a league, and preparing"
    " drafts to be rewritten, cut or thrown away. Other sections are"
    " assembled from the league's own data with no writing involved at all."
    " And some are written from scratch.",

    "Those are different things, so each section says which it was.",

    "## The line under each heading",

    "Every section in an issue carries a short line naming where it came"
    " from and whether the Commissioner edited it. There are six, and no"
    " others:",

    "\n".join([
        "- **Commish-written** — the Commissioner's words.",
        "- **Commish-written · AI-assisted** — the Commissioner's"
        " words, with AI help somewhere behind them.",
        "- **AI-generated** — drafted by AI and published without edits.",
        "- **AI-generated · Commish edited** — drafted by AI, then"
        " edited.",
        "- **Automatically generated** — assembled from league data, not"
        " written.",
        "- **Automatically generated · Commish edited** — assembled"
        " from data, then edited.",
    ]),

    "The line describes the section it sits under, not the issue as a whole."
    " One issue routinely carries several different ones.",

    "## Where the numbers come from",

    "Rosters, lineups, scores, standings, transactions and draft results"
    " come from Sleeper, where the leagues are played. Anything derived from"
    " them — power rankings, positional strength, draft value, records"
    " — is computed from that data, and the page it appears on says what"
    " it was measured against.",

    "Rankings and awards are editorial judgments rather than measurements."
    " Where a page shows a computed ordering beside the Commissioner's, it"
    " shows both and names the disagreement rather than settling it quietly.",

    "## The archive",

    "Published issues are permanent. An issue is frozen when it is published"
    " and is not rewritten afterwards. A correction is published as a new"
    " revision beside the original, and the issue says that it was updated"
    " and why. Older issues, including ones written before this site"
    " existed, are kept in the archive as they were written.",

    "## Your team",

    "Choosing your team stores that one choice in your own browser. There is"
    " no account and no sign-in, nothing is sent anywhere, and clearing it"
    " removes it.",
]

DEFAULT_ABOUT = "\n\n".join(_ABOUT_BLOCKS) + "\n"


def read_about(path: Path | None = None) -> str:
    p = path or ABOUT_PATH
    return p.read_text(encoding="utf-8") if p.exists() else DEFAULT_ABOUT


def write_about(text: str, path: Path | None = None) -> Path:
    p = path or ABOUT_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


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
