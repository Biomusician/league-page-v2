"""Commissioner's Desk — private, localhost-only FastAPI app.

V1 scope: the draft-review workflow (Story Board, award nominations,
preseason Peer and Near-Peer Competition, Track-as-Take) with decisions
persisted to SQLite. Server-rendered pages, plain POST forms, no JS.

Run: .venv/Scripts/python.exe scripts/desk.py  (binds 127.0.0.1 only)
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from leaguepage.adp import load_adp_for_league
from leaguepage.config import DB_PATH, LEAGUES, TEMPLATES_DIR, get_league
from leaguepage.draft_analysis import analyze_league_draft
from leaguepage.draft_awards import draft_award_nominations
from leaguepage.draft_stories import draft_story_candidates
from leaguepage.editorial import load_coalitions, load_managers
from leaguepage.matchup_packet import ROUGH_DRAFT_MARKER, compute_week, matchup_status, week_dir
from leaguepage.storage import Storage


def create_app(db_path: Path | str = DB_PATH) -> FastAPI:
    app = FastAPI(title="Commissioner's Desk", docs_url=None, redoc_url=None)
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

    def storage() -> Storage:
        return Storage(db_path)

    def _draft_context(league_slug: str, season: str) -> dict:
        league = get_league(league_slug)
        managers = load_managers()
        coalitions = load_coalitions()
        with storage() as s:
            analysis = analyze_league_draft(
                s, league, managers=managers, adp=load_adp_for_league(league)
            )
            candidates = (
                draft_story_candidates(analysis, storage=s, managers=managers, coalitions=coalitions)
                if analysis else []
            )
            story_decisions = s.get_story_decisions(league_slug, season, "draft")
            award_decisions = s.get_award_decisions(league_slug, season, "draft")
            power = s.get_power_rankings(league_slug, season, "preseason")
            takes = s.all_takes(league_slug, season)
            issue = s.get_issue(league_slug, season, "draft")
        awards = draft_award_nominations(analysis) if analysis else []
        power_by_roster = {p["roster_id"]: p for p in power}
        return {
            "league": league,
            "season": season,
            "analysis": analysis,
            "candidates": candidates,
            "story_decisions": story_decisions,
            "awards": awards,
            "award_decisions": award_decisions,
            "power_by_roster": power_by_roster,
            "takes": takes,
            "issue": issue,
        }

    @app.get("/")
    def root():
        return RedirectResponse("/commissioner", status_code=302)

    @app.get("/commissioner")
    def home(request: Request):
        cards = []
        with storage() as s:
            for league in LEAGUES:
                league_data = s.get_league(league.league_id) or {}
                season = str(league_data.get("season") or "")
                drafts = s.get_drafts_for_league(league.league_id)
                draft = drafts[0] if drafts else None
                picks = len(s.get_draft_picks(draft["draft_id"])) if draft else 0
                decided = len(s.get_story_decisions(league.slug, season, "draft"))
                awards_decided = len(s.get_award_decisions(league.slug, season, "draft"))
                issue = s.get_issue(league.slug, season, "draft")
                cards.append({
                    "league": league,
                    "season": season,
                    "current_week": int(s.get_meta("current_week") or 1),
                    "league_status": league_data.get("status"),
                    "draft_status": draft.get("status") if draft else "none",
                    "picks": picks,
                    "story_decisions": decided,
                    "award_decisions": awards_decided,
                    "issue_status": issue["status"] if issue else "not started",
                })
        return templates.TemplateResponse(request, "desk/home.html", {"cards": cards})

    @app.get("/commissioner/{league_slug}/{season}/draft-review")
    def draft_review(request: Request, league_slug: str, season: str):
        ctx = _draft_context(league_slug, season)
        ctx["request"] = request
        return templates.TemplateResponse(request, "desk/draft_review.html", ctx)

    @app.post("/commissioner/{league_slug}/{season}/draft-review/story")
    def decide_story(
        league_slug: str, season: str,
        candidate_id: str = Form(...), decision: str = Form(...), note: str = Form(""),
    ):
        with storage() as s:
            s.set_story_decision(
                league_slug=league_slug, season=season, workflow="draft",
                candidate_id=candidate_id, decision=decision, note=note.strip() or None,
            )
        return RedirectResponse(
            f"/commissioner/{league_slug}/{season}/draft-review#stories", status_code=303
        )

    @app.post("/commissioner/{league_slug}/{season}/draft-review/award")
    def decide_award(
        league_slug: str, season: str,
        award_key: str = Form(...), decision: str = Form(...),
        winner: str = Form(""), note: str = Form(""),
    ):
        with storage() as s:
            s.set_award_decision(
                league_slug=league_slug, season=season, workflow="draft",
                award_key=award_key, decision=decision,
                winner=winner.strip() or None, note=note.strip() or None,
            )
        return RedirectResponse(
            f"/commissioner/{league_slug}/{season}/draft-review#awards", status_code=303
        )

    @app.post("/commissioner/{league_slug}/{season}/draft-review/power")
    async def save_power(request: Request, league_slug: str, season: str):
        form = await request.form()
        entries = []
        for key, value in form.items():
            if key.startswith("rank_"):
                roster_id = int(key.removeprefix("rank_"))
                rank = int(value) if str(value).strip().isdigit() else None
                tier_raw = str(form.get(f"tier_{roster_id}", "")).strip()
                entries.append({
                    "roster_id": roster_id,
                    "rank": rank,
                    "tier": int(tier_raw) if tier_raw.isdigit() else None,
                    "note": str(form.get(f"note_{roster_id}", "")).strip() or None,
                })
        with storage() as s:
            s.save_power_rankings(league_slug, season, "preseason", entries)
        return RedirectResponse(
            f"/commissioner/{league_slug}/{season}/draft-review#power", status_code=303
        )

    @app.post("/commissioner/{league_slug}/{season}/draft-review/take")
    def add_take(
        league_slug: str, season: str,
        subject: str = Form(...), quote: str = Form(...),
        topic: str = Form(""), players: str = Form(""), confidence: str = Form(""),
    ):
        player_list = [p.strip() for p in players.split(",") if p.strip()]
        with storage() as s:
            s.add_take(
                league_slug=league_slug, season=season, week=None,
                context="draft", source="draft-review", author="commissioner",
                subject=subject.strip(), quote=quote.strip(),
                topic=topic.strip() or None, players=player_list or None,
                confidence=confidence.strip() or None,
            )
        return RedirectResponse(
            f"/commissioner/{league_slug}/{season}/draft-review#takes", status_code=303
        )

    @app.post("/commissioner/{league_slug}/{season}/draft-review/take/{take_id}/resolve")
    def resolve_take(
        league_slug: str, season: str, take_id: int,
        status: str = Form(...), resolution: str = Form(""),
    ):
        with storage() as s:
            s.resolve_take(take_id, status, resolution.strip() or None)
        return RedirectResponse(
            f"/commissioner/{league_slug}/{season}/draft-review#takes", status_code=303
        )

    # ------------------------------------------------------------------
    # Matchup Lab
    # ------------------------------------------------------------------

    def _week_board(league_slug: str, week: int):
        league = get_league(league_slug)
        with storage() as s:
            computed = compute_week(s, league, week)
        return league, computed

    def _mdir(league, season: str, week: int, slug: str) -> Path:
        return week_dir(league, season, week) / "matchups" / slug

    @app.get("/commissioner/{league_slug}/{season}/week/{week}/matchups")
    def matchup_queue(request: Request, league_slug: str, season: str, week: int):
        league, computed = _week_board(league_slug, week)
        rows = []
        if computed:
            for sm in computed["scored"]:
                slug = sm["matchup"]["matchup_slug"]
                draft_path = _mdir(league, season, week, slug) / "draft.md"
                rows.append({
                    **sm,
                    "slug": slug,
                    "status": matchup_status(sm["state"], draft_path.exists()),
                    "effective_prominence": (sm["state"] or {}).get("prominence_override")
                                            or sm["recommended_prominence"],
                })
        return templates.TemplateResponse(request, "desk/matchup_queue.html", {
            "league": league, "season": season, "week": week,
            "computed": computed, "rows": rows,
        })

    @app.get("/commissioner/{league_slug}/{season}/week/{week}/matchups/{slug}")
    def matchup_detail(request: Request, league_slug: str, season: str, week: int, slug: str):
        league, computed = _week_board(league_slug, week)
        sm = next((x for x in (computed or {}).get("scored", [])
                   if x["matchup"]["matchup_slug"] == slug), None)
        draft_path = _mdir(league, season, week, slug) / "draft.md"
        draft_text = draft_path.read_text(encoding="utf-8") if draft_path.exists() else ""
        with storage() as s:
            angle_decisions = s.get_story_decisions(league_slug, season, f"week-{week:02d}")
        return templates.TemplateResponse(request, "desk/matchup_detail.html", {
            "league": league, "season": season, "week": week, "slug": slug,
            "sm": sm,
            "status": matchup_status((sm or {}).get("state"), bool(draft_text)) if sm else None,
            "draft_text": draft_text,
            "marker_present": ROUGH_DRAFT_MARKER in draft_text,
            "angle_decisions": angle_decisions,
        })

    def _back(league_slug, season, week, slug, anchor=""):
        return RedirectResponse(
            f"/commissioner/{league_slug}/{season}/week/{week}/matchups/{slug}{anchor}",
            status_code=303)

    @app.post("/commissioner/{league_slug}/{season}/week/{week}/matchups/{slug}/angle")
    def matchup_angle(
        league_slug: str, season: str, week: int, slug: str,
        angle_id: str = Form(""), action: str = Form(...),
        custom_angle: str = Form(""), note: str = Form(""),
    ):
        with storage() as s:
            if action == "select" and angle_id:
                s.set_matchup_state(league_slug=league_slug, season=season, week=week,
                                    matchup_slug=slug, selected_angle_id=angle_id,
                                    custom_angle=None, status="ready_to_draft")
                s.set_story_decision(league_slug=league_slug, season=season,
                                     workflow=f"week-{week:02d}", candidate_id=angle_id,
                                     decision="include", note=note.strip() or None)
            elif action in ("save", "reject") and angle_id:
                s.set_story_decision(league_slug=league_slug, season=season,
                                     workflow=f"week-{week:02d}", candidate_id=angle_id,
                                     decision="save" if action == "save" else "ignore",
                                     note=note.strip() or None)
            elif action == "custom" and custom_angle.strip():
                s.set_matchup_state(league_slug=league_slug, season=season, week=week,
                                    matchup_slug=slug, custom_angle=custom_angle.strip(),
                                    selected_angle_id=None, status="ready_to_draft")
            elif action == "note":
                s.set_matchup_state(league_slug=league_slug, season=season, week=week,
                                    matchup_slug=slug, angle_note=note.strip() or None)
            elif action == "stale" and angle_id:
                s.set_story_decision(league_slug=league_slug, season=season,
                                     workflow=f"week-{week:02d}", candidate_id=angle_id,
                                     decision="ignore", note="marked stale")
        return _back(league_slug, season, week, slug, "#story")

    @app.post("/commissioner/{league_slug}/{season}/week/{week}/matchups/{slug}/prominence")
    def matchup_prominence(
        league_slug: str, season: str, week: int, slug: str, prominence: str = Form(...),
    ):
        override = prominence if prominence in ("FEATURE", "MAJOR", "STANDARD", "CAPSULE") else None
        with storage() as s:
            s.set_matchup_state(league_slug=league_slug, season=season, week=week,
                                matchup_slug=slug, prominence_override=override)
        return _back(league_slug, season, week, slug)

    @app.post("/commissioner/{league_slug}/{season}/week/{week}/matchups/{slug}/draft")
    def matchup_draft_save(
        league_slug: str, season: str, week: int, slug: str, draft_text: str = Form(...),
    ):
        league = get_league(league_slug)
        path = _mdir(league, season, week, slug) / "draft.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(draft_text.replace("\r\n", "\n"), encoding="utf-8")
        with storage() as s:
            s.set_matchup_state(league_slug=league_slug, season=season, week=week,
                                matchup_slug=slug, status="edited")
        return _back(league_slug, season, week, slug, "#draft")

    @app.post("/commissioner/{league_slug}/{season}/week/{week}/matchups/{slug}/status")
    def matchup_status_change(
        league_slug: str, season: str, week: int, slug: str, action: str = Form(...),
    ):
        league = get_league(league_slug)
        draft_path = _mdir(league, season, week, slug) / "draft.md"
        text = draft_path.read_text(encoding="utf-8") if draft_path.exists() else ""
        transitions = {"approve": "approved", "unapprove": "edited", "lock": "locked",
                       "reject": "rejected", "requeue": "ready_to_draft"}
        status = transitions.get(action)
        if status in ("approved", "locked") and (not text or ROUGH_DRAFT_MARKER in text):
            # refuse to bless an empty or still-rough draft
            return _back(league_slug, season, week, slug, "#draft")
        if status:
            with storage() as s:
                s.set_matchup_state(league_slug=league_slug, season=season, week=week,
                                    matchup_slug=slug, status=status)
                if status == "approved":
                    # feed the repetition log from the draft's usage comment
                    import re as _re

                    m = _re.search(r"<!--\s*usage:\s*(.+?)\s*-->", text)
                    if m:
                        for part in m.group(1).split():
                            if "=" not in part:
                                continue
                            key, _, value = part.partition("=")
                            kind = {"angle": "angle", "frame": "frame",
                                    "callback": "callback", "joke_family": "joke_family",
                                    "bit": "bit"}.get(key)
                            if kind and value and value.lower() != "none":
                                s.log_editorial_usage(
                                    league_slug=league_slug, season=season, week=week,
                                    matchup_slug=slug, kind=kind, value=value,
                                    note="logged on approval")
        return _back(league_slug, season, week, slug, "#draft")

    @app.post("/commissioner/{league_slug}/{season}/week/{week}/matchups/{slug}/revision")
    def matchup_revision(
        league_slug: str, season: str, week: int, slug: str,
        request_type: str = Form(...), detail: str = Form(""),
    ):
        with storage() as s:
            state = s.get_matchup_state(league_slug, season, week, slug) or {}
            existing = state.get("revision_requests")
            reqs = json.loads(existing) if existing else []
            entry = request_type + (f": {detail.strip()}" if detail.strip() else "")
            reqs.append(entry)
            s.set_matchup_state(league_slug=league_slug, season=season, week=week,
                                matchup_slug=slug, revision_requests=reqs,
                                status="ready_to_draft")
        return _back(league_slug, season, week, slug, "#draft")

    return app
