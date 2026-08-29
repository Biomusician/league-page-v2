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
from leaguepage.issue_builder import (
    assemble_issue, build_lowdown_prep, build_section_authoring, issue_dir,
    module_states, write_authoring_index,
)
from leaguepage.matchup_packet import ROUGH_DRAFT_MARKER, compute_week, matchup_status, week_dir
from leaguepage.storage import Storage
from leaguepage.team_names import resolve_public_names
from leaguepage.weekly_awards import weekly_award_nominations
from leaguepage.weekly_signals import weekly_story_candidates


def _week_of(issue_key: str) -> int | None:
    return int(issue_key.removeprefix("week-")) if issue_key.startswith("week-") else None


DEFAULT_PORT = 8026


def probe_health(port: int, timeout: float = 2.0) -> dict | None:
    """Return /health JSON if a Commissioner's Desk answers on this port."""
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
        return data if data.get("app") == "commissioner-desk" else None
    except Exception:
        return None


def port_is_free(port: int) -> bool:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def pick_port(preferred: int = DEFAULT_PORT) -> tuple[int, str]:
    """(port, situation) where situation is 'free', 'already-running'
    (a healthy Desk owns the preferred port), or 'fallback' (a foreign
    process owns it; a nearby free port was chosen instead)."""
    if port_is_free(preferred):
        return preferred, "free"
    if probe_health(preferred):
        return preferred, "already-running"
    for candidate in range(preferred + 1, preferred + 21):
        if port_is_free(candidate):
            return candidate, "fallback"
    raise RuntimeError(f"No free port found in {preferred}-{preferred + 20}.")


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

    @app.get("/health")
    def health():
        """Launcher readiness probe. Status facts only — no private values."""
        try:
            with storage() as s:
                leagues_loaded = sum(1 for lg in LEAGUES if s.get_league(lg.league_id))
                season = None
                for lg in LEAGUES:
                    data = s.get_league(lg.league_id) or {}
                    season = season or data.get("season")
            return {
                "status": "ok",
                "app": "commissioner-desk",
                "database": "ok",
                "season": season,
                "leagues_loaded": leagues_loaded,
                "leagues_configured": len(LEAGUES),
            }
        except Exception as exc:
            return {"status": "error", "app": "commissioner-desk",
                    "error": type(exc).__name__}

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
                wk = int(s.get_meta("current_week") or 1)
                week_issue = s.get_issue(league.slug, season, f"week-{wk:02d}")
                cards.append({
                    "week_issue_status": week_issue["status"] if week_issue else "not started",
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

    # ------------------------------------------------------------------
    # Phase 6: Issue workspace (weekly and draft issues share these routes)
    # ------------------------------------------------------------------

    def _candidates_for(s: Storage, league, season: str, issue_key: str) -> list[dict]:
        week = _week_of(issue_key)
        coalitions = load_coalitions()
        if week is not None:
            computed = compute_week(s, league, week)
            if not computed:
                return []
            return weekly_story_candidates(s, league, week, computed, coalitions=coalitions)
        analysis = analyze_league_draft(s, league, managers=load_managers(),
                                        adp=load_adp_for_league(league))
        if not analysis:
            return []
        return draft_story_candidates(analysis, storage=s, managers=load_managers(),
                                      coalitions=coalitions)

    def _awards_for(s: Storage, league, season: str, issue_key: str) -> list[dict]:
        week = _week_of(issue_key)
        if week is not None:
            ranks = {p["roster_id"]: p["rank"]
                     for p in s.get_power_rankings(league.slug, season, "preseason")
                     if p.get("rank")}
            return weekly_award_nominations(s, league, week, preseason_ranks=ranks or None)
        analysis = analyze_league_draft(s, league, managers=load_managers(),
                                        adp=load_adp_for_league(league))
        return draft_award_nominations(analysis) if analysis else []

    def _workspace_context(league_slug: str, season: str, issue_key: str) -> dict:
        league = get_league(league_slug)
        week = _week_of(issue_key)
        with storage() as s:
            candidates = _candidates_for(s, league, season, issue_key)
            decisions = s.get_story_decisions(league_slug, season, issue_key)
            awards = _awards_for(s, league, season, issue_key)
            award_decisions = s.get_award_decisions(league_slug, season, issue_key)
            modules = module_states(s, league, season, issue_key, week=week)
            issue_row = s.get_issue(league_slug, season, issue_key)
            resolved = resolve_public_names(s, league)
            try:
                assembled = assemble_issue(s, league, season, issue_key, week=week)
                warnings = assembled["warnings"]
            except Exception as exc:  # assembly itself should not 500 the desk
                assembled, warnings = None, [str(exc)]
        unresolved = [rid for rid, v in resolved.items() if v["name"] is None]
        decided_awards = sum(1 for d in award_decisions.values()
                             if d["decision"] in ("awarded", "manual"))
        lowdown = next((m for m in modules if m["module_key"] == "lowdown"), None)
        ctp = next((m for m in modules if m["module_key"] == "ctp"), None)
        stages = [
            ("DATA", "ready" if candidates or week is None else "not ready",
             f"/commissioner/{league_slug}/{season}/issue/{issue_key}"),
            ("STORIES", f"{len(candidates)} candidates, "
                        f"{sum(1 for d in decisions.values() if d['decision'] == 'include')} selected",
             f"/commissioner/{league_slug}/{season}/issue/{issue_key}/stories"),
            ("MATCHUPS", (ctp["detail"] if ctp and week is not None else "n/a"),
             f"/commissioner/{league_slug}/{season}/week/{week}/matchups" if week else None),
            ("AWARDS", f"{decided_awards} decided of "
                       f"{sum(1 for a in awards if a['nominees'])} with nominees",
             f"/commissioner/{league_slug}/{season}/issue/{issue_key}/awards"),
            ("LOWDOWN", lowdown["status"] if lowdown else "n/a",
             f"/commissioner/{league_slug}/{season}/issue/{issue_key}/lowdown"),
            ("ISSUE", f"{len(warnings)} blocking warning(s)" if warnings else "assembles clean",
             f"/commissioner/{league_slug}/{season}/issue/{issue_key}/builder"),
            ("PUBLISH", (issue_row or {}).get("status") or "not started",
             f"/commissioner/{league_slug}/{season}/issue/{issue_key}/builder"),
        ]
        return {
            "league": league, "season": season, "issue_key": issue_key, "week": week,
            "stages": stages, "candidates": candidates, "decisions": decisions,
            "awards": awards, "award_decisions": award_decisions,
            "modules": modules, "warnings": warnings, "issue_row": issue_row,
            "resolved": resolved, "unresolved": unresolved,
        }

    @app.get("/commissioner/{league_slug}/{season}/issue/{issue_key}")
    def issue_workspace(request: Request, league_slug: str, season: str, issue_key: str):
        ctx = _workspace_context(league_slug, season, issue_key)
        return templates.TemplateResponse(request, "desk/workspace.html", ctx)

    @app.post("/commissioner/{league_slug}/{season}/issue/{issue_key}/build")
    def issue_build(league_slug: str, season: str, issue_key: str):
        league = get_league(league_slug)
        week = _week_of(issue_key)
        with storage() as s:
            if week is not None:
                from leaguepage.matchup_packet import build_weekly_packet

                build_weekly_packet(s, league, week)
            candidates = _candidates_for(s, league, season, issue_key)
            awards = _awards_for(s, league, season, issue_key)
            build_lowdown_prep(s, league, season, issue_key, candidates)
            build_section_authoring(s, league, season, issue_key, candidates, awards)
            write_authoring_index(league, season, issue_key)
            if not s.get_issue(league_slug, season, issue_key):
                s.set_issue_status(league_slug=league_slug, season=season,
                                   issue_key=issue_key, status="generated")
        return RedirectResponse(
            f"/commissioner/{league_slug}/{season}/issue/{issue_key}", status_code=303)

    @app.post("/commissioner/{league_slug}/{season}/team-names")
    async def set_team_names(request: Request, league_slug: str, season: str):
        form = await request.form()
        back = str(form.get("back") or f"/commissioner/{league_slug}/{season}/issue/draft")
        with storage() as s:
            for key, value in form.items():
                if key.startswith("name_") and str(value).strip():
                    s.set_public_team_name(league_slug, int(key.removeprefix("name_")),
                                           str(value))
        return RedirectResponse(back, status_code=303)

    @app.post("/commissioner/{league_slug}/{season}/issue/{issue_key}/theme")
    def set_theme(league_slug: str, season: str, issue_key: str, theme: str = Form("")):
        with storage() as s:
            s.set_issue_theme(league_slug, season, issue_key, theme.strip() or None)
        return RedirectResponse(
            f"/commissioner/{league_slug}/{season}/issue/{issue_key}", status_code=303)

    @app.get("/commissioner/{league_slug}/{season}/issue/{issue_key}/review")
    def review_packet(request: Request, league_slug: str, season: str, issue_key: str):
        import markdown as md

        from leaguepage.review_packet import build_review_packet

        league = get_league(league_slug)
        with storage() as s:
            candidates = _candidates_for(s, league, season, issue_key)
            awards = _awards_for(s, league, season, issue_key)
            path = build_review_packet(s, league, season, issue_key,
                                       awards=awards, candidates=candidates)
        html = md.markdown(path.read_text(encoding="utf-8"), extensions=["tables"])
        return templates.TemplateResponse(request, "desk/review.html", {
            "league": league, "season": season, "issue_key": issue_key,
            "packet_html": html, "packet_path": path.as_posix(),
        })

    @app.get("/commissioner/{league_slug}/{season}/issue/{issue_key}/stories")
    def story_board(request: Request, league_slug: str, season: str, issue_key: str):
        ctx = _workspace_context(league_slug, season, issue_key)
        return templates.TemplateResponse(request, "desk/stories.html", ctx)

    @app.post("/commissioner/{league_slug}/{season}/issue/{issue_key}/stories")
    def story_decide(
        league_slug: str, season: str, issue_key: str,
        candidate_id: str = Form(...), decision: str = Form(...),
        route: str = Form(""), note: str = Form(""),
    ):
        with storage() as s:
            s.set_story_decision(
                league_slug=league_slug, season=season, workflow=issue_key,
                candidate_id=candidate_id, decision=decision,
                route=route.strip() or None, note=note.strip() or None)
        return RedirectResponse(
            f"/commissioner/{league_slug}/{season}/issue/{issue_key}/stories", status_code=303)

    @app.get("/commissioner/{league_slug}/{season}/issue/{issue_key}/awards")
    def awards_board(request: Request, league_slug: str, season: str, issue_key: str):
        ctx = _workspace_context(league_slug, season, issue_key)
        slate = {"strong": [], "possible": [], "none": []}
        for aw in ctx["awards"]:
            slate[aw.get("slate") or ("possible" if aw["nominees"] else "none")].append(aw["award_name"])
        ctx["slate"] = slate
        return templates.TemplateResponse(request, "desk/awards.html", ctx)

    @app.post("/commissioner/{league_slug}/{season}/issue/{issue_key}/awards")
    def award_decide(
        league_slug: str, season: str, issue_key: str,
        award_key: str = Form(...), decision: str = Form(...),
        winner: str = Form(""), note: str = Form(""),
    ):
        with storage() as s:
            s.set_award_decision(
                league_slug=league_slug, season=season, workflow=issue_key,
                award_key=award_key, decision=decision,
                winner=winner.strip() or None, note=note.strip() or None)
        return RedirectResponse(
            f"/commissioner/{league_slug}/{season}/issue/{issue_key}/awards", status_code=303)

    @app.get("/commissioner/{league_slug}/{season}/issue/{issue_key}/lowdown")
    def lowdown_screen(request: Request, league_slug: str, season: str, issue_key: str):
        league = get_league(league_slug)
        idir = issue_dir(league, season, issue_key)
        files = {}
        for name in ("PREP.md", "AUTHORING.md", "themes.md", "outline.md",
                     "rough-lowdown.md", "lowdown.md"):
            p = idir / "lowdown" / name
            files[name] = p.read_text(encoding="utf-8") if p.exists() else None
        ctx = _workspace_context(league_slug, season, issue_key)
        ctx["files"] = files
        ctx["lowdown_dir"] = (idir / "lowdown").as_posix()
        return templates.TemplateResponse(request, "desk/lowdown.html", ctx)

    @app.post("/commissioner/{league_slug}/{season}/issue/{issue_key}/lowdown")
    def lowdown_save(
        league_slug: str, season: str, issue_key: str,
        lowdown_text: str = Form(""), action: str = Form("save"),
    ):
        league = get_league(league_slug)
        path = issue_dir(league, season, issue_key) / "lowdown" / "lowdown.md"
        with storage() as s:
            if action == "save" and lowdown_text.strip():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(lowdown_text.replace("\r\n", "\n"), encoding="utf-8")
            elif action == "approve":
                text = path.read_text(encoding="utf-8") if path.exists() else ""
                if text and ROUGH_DRAFT_MARKER not in text:
                    s.set_issue_module(league_slug=league_slug, season=season,
                                       issue_key=issue_key, module_key="lowdown", approved=1)
            elif action == "unapprove":
                s.set_issue_module(league_slug=league_slug, season=season,
                                   issue_key=issue_key, module_key="lowdown", approved=0)
        return RedirectResponse(
            f"/commissioner/{league_slug}/{season}/issue/{issue_key}/lowdown", status_code=303)

    @app.get("/commissioner/{league_slug}/{season}/issue/{issue_key}/builder")
    def issue_builder_screen(request: Request, league_slug: str, season: str, issue_key: str):
        ctx = _workspace_context(league_slug, season, issue_key)
        return templates.TemplateResponse(request, "desk/builder.html", ctx)

    @app.post("/commissioner/{league_slug}/{season}/issue/{issue_key}/builder/module")
    def issue_module_update(
        league_slug: str, season: str, issue_key: str,
        module_key: str = Form(...), action: str = Form(...),
        position: str = Form(""), custom_title: str = Form(""),
    ):
        fields: dict = {}
        if action == "include":
            fields["included"] = 1
        elif action == "exclude":
            fields["included"] = 0
        elif action == "approve":
            fields["approved"] = 1
        elif action == "unapprove":
            fields["approved"] = 0
        elif action == "retitle":
            fields["custom_title"] = custom_title.strip() or None
        elif action == "move" and position.strip().lstrip("-").isdigit():
            fields["position"] = int(position)
        if fields:
            with storage() as s:
                s.set_issue_module(league_slug=league_slug, season=season,
                                   issue_key=issue_key, module_key=module_key, **fields)
        return RedirectResponse(
            f"/commissioner/{league_slug}/{season}/issue/{issue_key}/builder", status_code=303)

    @app.get("/commissioner/{league_slug}/{season}/issue/{issue_key}/preview")
    def issue_preview(request: Request, league_slug: str, season: str, issue_key: str):
        import markdown as md

        league = get_league(league_slug)
        with storage() as s:
            assembled = assemble_issue(s, league, season, issue_key, week=_week_of(issue_key))
        sections = [
            {"title": x["title"],
             "html": md.markdown(x["content_md"], extensions=["tables", "smarty"])
             if x.get("content_md") else "<p><i>(no content)</i></p>",
             "approved": x["approved"]}
            for x in assembled["sections"] if x["kind"] != "auto"
        ]
        return templates.TemplateResponse(request, "desk/preview.html", {
            "league": league, "season": season, "issue_key": issue_key,
            "sections": sections, "warnings": assembled["warnings"],
        })

    @app.post("/commissioner/{league_slug}/{season}/issue/{issue_key}/publish")
    def issue_publish(league_slug: str, season: str, issue_key: str):
        from leaguepage.publish import PublishError, publish_assembled_issue

        league = get_league(league_slug)
        with storage() as s:
            try:
                publish_assembled_issue(s, league, season, issue_key, week=_week_of(issue_key))
            except PublishError:
                return RedirectResponse(
                    f"/commissioner/{league_slug}/{season}/issue/{issue_key}/builder",
                    status_code=303)
        return RedirectResponse(
            f"/commissioner/{league_slug}/{season}/issue/{issue_key}", status_code=303)

    @app.get("/commissioner/{league_slug}/{season}/rankings/{label}")
    def rankings_screen(request: Request, league_slug: str, season: str, label: str):
        league = get_league(league_slug)
        with storage() as s:
            resolved = resolve_public_names(s, league)
            rosters = s.get_rosters(league.league_id)
            current = {p["roster_id"]: p for p in s.get_power_rankings(league_slug, season, label)}
            previous_label = "preseason" if label != "preseason" else None
            previous = ({p["roster_id"]: p for p in
                         s.get_power_rankings(league_slug, season, previous_label)}
                        if previous_label else {})
        teams = []
        for r in rosters:
            st = r.get("settings") or {}
            teams.append({
                "roster_id": r["roster_id"],
                "name": resolved[r["roster_id"]]["name"] or f"Roster {r['roster_id']}",
                "record": f"{st.get('wins', 0)}-{st.get('losses', 0)}",
                "fpts": round(float(st.get("fpts", 0)) + float(st.get("fpts_decimal", 0)) / 100, 1),
                "current": current.get(r["roster_id"]),
                "previous": previous.get(r["roster_id"]),
            })
        teams.sort(key=lambda t: (t["current"] or {}).get("rank") or 99)
        return templates.TemplateResponse(request, "desk/rankings.html", {
            "league": league, "season": season, "label": label, "teams": teams,
        })

    @app.post("/commissioner/{league_slug}/{season}/rankings/{label}")
    async def rankings_save(request: Request, league_slug: str, season: str, label: str):
        form = await request.form()
        entries = []
        for key, value in form.items():
            if key.startswith("rank_"):
                rid = int(key.removeprefix("rank_"))
                entries.append({
                    "roster_id": rid,
                    "rank": int(value) if str(value).strip().isdigit() else None,
                    "tier": int(form.get(f"tier_{rid}")) if str(form.get(f"tier_{rid}", "")).strip().isdigit() else None,
                    "note": str(form.get(f"note_{rid}", "")).strip() or None,
                })
        with storage() as s:
            s.save_power_rankings(league_slug, season, label, entries)
        return RedirectResponse(
            f"/commissioner/{league_slug}/{season}/rankings/{label}", status_code=303)

    @app.get("/commissioner/{league_slug}/{season}/false-assumptions")
    def false_assumptions(request: Request, league_slug: str, season: str):
        league = get_league(league_slug)
        with storage() as s:
            takes = s.all_takes(league_slug, season)
        return templates.TemplateResponse(request, "desk/false_assumptions.html", {
            "league": league, "season": season, "takes": takes,
        })

    @app.post("/commissioner/{league_slug}/{season}/false-assumptions/{take_id}")
    def false_assumption_decide(
        league_slug: str, season: str, take_id: int,
        action: str = Form(...), resolution: str = Form(""), issue_key: str = Form(""),
    ):
        with storage() as s:
            if action == "use" and issue_key:
                s.set_story_decision(
                    league_slug=league_slug, season=season, workflow=issue_key,
                    candidate_id=f"story:take:{take_id}", decision="include",
                    route="false-assumptions",
                    note="commissioner: use as False Assumption")
            elif action == "too_early":
                s.resolve_take(take_id, "too_early", resolution.strip() or None)
            elif action == "validate":
                s.resolve_take(take_id, "validated", resolution.strip() or None)
            elif action == "retire":
                s.resolve_take(take_id, "retired", resolution.strip() or None)
            # "ignore" records nothing — the take stays untouched
        return RedirectResponse(
            f"/commissioner/{league_slug}/{season}/false-assumptions", status_code=303)

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

    from leaguepage.desk_editor import register_editor

    register_editor(app, storage, templates)

    return app
