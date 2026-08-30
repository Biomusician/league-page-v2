"""Whole-issue editor for the Commissioner's Desk.

One screen per issue: every included section as an editable card, per-card
autosave with conflict detection, approval, revision history with restore,
structured rewrite requests for Claude Code, side-by-side proposal review,
publication blockers, full private preview, and gated publish / deploy.

Content model (unchanged from the issue builder):
  lowdown/lowdown.md        commissioner-owned final Lowdown
  sections/<module>.md      section prose
  matchups/<slug>/draft.md  weekly matchup previews (weekly issues only)
  proposals/<section>.md    Claude rewrite proposals awaiting accept/discard
The DB adds: prose_revisions (undo), section_prose_state
(generated vs commissioner-edited), issue_revision_requests (rewrite queue).
"""
from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

from fastapi import Form, Request
from fastapi.responses import JSONResponse, RedirectResponse

from leaguepage.config import DIST_DIR, REPO_ROOT, get_league
from leaguepage.issue_builder import (
    BLOCKED_MARKERS, assemble_issue, issue_dir, module_states,
)
from leaguepage.matchup_packet import compute_week, matchup_status, week_dir
from leaguepage.team_names import resolve_public_names

PRODUCTION_URL = "https://league-page-ten-sandy.vercel.app"
VERCEL_PROJECT = "league-page"

_SECTION_RE = re.compile(r"^[a-z0-9-]+$")
_MATCHUP_RE = re.compile(r"^matchup:([a-z0-9-]+)$")


def _week_of(issue_key: str) -> int | None:
    return int(issue_key.removeprefix("week-")) if issue_key.startswith("week-") else None


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _split_chunks(text: str) -> list[str]:
    """Split on lines beginning '### ' when there are 2+, else one chunk.
    Chunks are exact substrings; ''.join(chunks) == text (lossless)."""
    starts = [m.start() for m in re.finditer(r"(?m)^### ", text)]
    if len(starts) < 2:
        return [text]
    bounds = ([0] if starts[0] != 0 else []) + starts + [len(text)]
    return [text[a:b] for a, b in zip(bounds, bounds[1:])]


def _chunk_heading(chunk: str) -> str:
    m = re.search(r"(?m)^### +(.+)$", chunk)
    return m.group(1).strip() if m else "(intro)"


def register_editor(app, storage, templates) -> None:  # noqa: C901 - route registry
    """Attach editor routes. `storage` is the desk's Storage factory."""

    def _paths(league, season: str, issue_key: str):
        idir = issue_dir(league, season, issue_key)
        return idir, idir / "proposals"

    def _proposal_path(idir: Path, section: str) -> Path:
        # ':' is illegal in Windows filenames; matchup sections map to '--'
        return idir / "proposals" / f"{section.replace(':', '--')}.md"

    def _section_path(league, season: str, issue_key: str, section: str) -> Path | None:
        idir = issue_dir(league, season, issue_key)
        m = _MATCHUP_RE.match(section)
        if m:
            week = _week_of(issue_key)
            if week is None:
                return None
            p = week_dir(league, season, week) / "matchups" / m.group(1) / "draft.md"
        elif section == "lowdown":
            p = idir / "lowdown" / "lowdown.md"
        elif _SECTION_RE.match(section):
            p = idir / "sections" / f"{section}.md"
        else:
            return None
        base = idir.parent.parent.parent.resolve()  # editorial/
        if not p.resolve().is_relative_to(base):
            return None
        return p

    def _read(p: Path | None) -> str | None:
        return p.read_text(encoding="utf-8") if p and p.exists() else None

    def _blockers(s, league, season: str, issue_key: str, modules: list[dict]) -> list[dict]:
        assembled = assemble_issue(s, league, season, issue_key, week=_week_of(issue_key))
        by_title = {m["title"]: m["module_key"] for m in modules}
        out = []
        for w in assembled["warnings"]:
            anchor = "sec-team-names"
            m = re.search(r"[Mm]odule '([^']+)'", w)
            if m and m.group(1) in by_title:
                anchor = f"sec-{by_title[m.group(1)]}"
            out.append({"text": w, "anchor": anchor})
        return out

    def _editor_context(league_slug: str, season: str, issue_key: str) -> dict:
        from leaguepage.ghost_briefs import brief_for_section

        league = get_league(league_slug)
        week = _week_of(issue_key)
        idir, pdir = _paths(league, season, issue_key)
        with storage() as s:
            modules = module_states(s, league, season, issue_key, week=week)
            prose_rows = s.get_prose_state_rows(league_slug, season, issue_key)
            prose_states = {k: v["state"] for k, v in prose_rows.items()}
            issue_row = s.get_issue(league_slug, season, issue_key)
            resolved = resolve_public_names(s, league)
            blockers = _blockers(s, league, season, issue_key, modules)
            open_requests = s.list_rewrite_requests(league_slug, season, issue_key)
            rev_counts = {sec: len(s.get_prose_revisions(league_slug, season, issue_key, sec, limit=50))
                          for sec in set(prose_states) | {m["module_key"] for m in modules}}
            label = "preseason" if week is None else issue_key
            rankings = s.get_power_rankings(league_slug, season, label)

            def _brief(section: str) -> dict:
                b = brief_for_section(s, league, season, issue_key, section, week)
                written_at = (prose_rows.get(section) or {}).get("updated_at")
                b["stale_prose"] = bool(written_at and b.get("data_as_of")
                                        and b["data_as_of"] > written_at)
                return b

            matchup_cards = []
            if week is not None:
                computed = compute_week(s, league, week)
                states = s.list_matchup_states(league_slug, season, week)
                for sm in (computed or {}).get("scored", []):
                    slug = sm["matchup"]["matchup_slug"]
                    dpath = week_dir(league, season, week) / "matchups" / slug / "draft.md"
                    text = _read(dpath) or ""
                    st = states.get(slug) or {}
                    section = f"matchup:{slug}"
                    matchup_cards.append({
                        "slug": slug, "section": section,
                        "title": " vs ".join(
                            resolved.get(t["roster_id"], {}).get("name") or f"Roster {t['roster_id']}"
                            for t in sm["matchup"]["teams"]),
                        "text": text, "sha": _sha(text),
                        "status": matchup_status(st, bool(text.strip())),
                        "prominence": st.get("prominence_override") or sm.get("prominence"),
                        "angle": st.get("custom_angle") or st.get("selected_angle_id") or "(no angle)",
                        "brief": _brief(section),
                        "proposal": _read(_proposal_path(idir, section)),
                    })
            briefs = {m["module_key"]: _brief(m["module_key"])
                      for m in modules if m["kind"] in ("lowdown", "section", "power")}
        requests_by_section: dict[str, list[dict]] = {}
        for r in open_requests:
            requests_by_section.setdefault(r["section"], []).append(r)

        cards = []
        for m in modules:
            key, kind = m["module_key"], m["kind"]
            card = {**m, "anchor": f"sec-{key}", "editable": False,
                    "prose_state": prose_states.get(key, "generated"),
                    "requests": requests_by_section.get(key, []),
                    "revisions": rev_counts.get(key, 0), "proposal": None,
                    "brief": briefs.get(key)}
            if kind in ("lowdown", "section"):
                path = _section_path(league, season, issue_key, key)
                text = _read(path)
                card["editable"] = True
                card["not_written"] = not (text or "").strip()
                text = text or ""
                card["file_sha"] = _sha(text)
                chunks = _split_chunks(text) if kind == "section" else [text]
                card["chunks"] = [{"index": i, "text": c, "sha": _sha(c),
                                   "heading": _chunk_heading(c) if len(chunks) > 1 else None}
                                  for i, c in enumerate(chunks)]
                card["chunk_count"] = len(chunks)
                card["proposal"] = _read(_proposal_path(idir, key))
                if kind == "lowdown":
                    card["generated_source"] = _read(idir / "lowdown" / "rough-lowdown.md")
            elif kind == "power":
                card["rankings"] = rankings
                card["label"] = "preseason" if week is None else issue_key
            cards.append(card)

        name_rows = []
        for rid in sorted(resolved):
            v = resolved[rid]
            neutral = v["name"] is None or re.fullmatch(r"Roster \d+", v["name"] or "")
            name_rows.append({"roster_id": rid, "name": v["name"], "source": v["source"],
                              "neutral": bool(neutral)})
        base = f"/commissioner/{league_slug}/{season}/issue/{issue_key}"
        return {
            "league": league, "season": season, "issue_key": issue_key, "week": week,
            "cards": cards, "matchup_cards": matchup_cards, "blockers": blockers,
            "issue_row": issue_row, "name_rows": name_rows,
            "open_request_count": len(open_requests),
            "base": base, "edit_base": f"{base}/edit",
            "production_url": PRODUCTION_URL,
        }

    def _write_requests_file(s, league, season: str, issue_key: str) -> None:
        idir = issue_dir(league, season, issue_key)
        rows = s.list_rewrite_requests(league.slug, season, issue_key)
        path = idir / "REVISION_REQUESTS.md"
        if not rows:
            if path.exists():
                path.unlink()
            return
        lines = [
            f"# Pending rewrite requests — {league.display_name} {season} {issue_key}",
            "",
            "Claude Code: read `.claude/skills/my-writing-style/SKILL.md` first and",
            "follow it. For each request below, write a FULL replacement for the",
            "section to `proposals/<section>.md` inside this issue directory",
            "(matchup sections use `proposals/matchup--<slug>.md`).",
            "Do NOT edit the section files directly: the commissioner's current text",
            "is authoritative until a proposal is explicitly accepted on the Desk.",
            "Facts still come only from the issue's generated briefs and evidence.",
            "",
        ]
        for r in rows:
            lines.append(f"- [request {r['id']}] `{r['section']}`: {r['note']}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # ------------------------------------------------------------ pages

    @app.get("/commissioner/{league_slug}/{season}/issue/{issue_key}/edit")
    def issue_edit(request: Request, league_slug: str, season: str, issue_key: str):
        ctx = _editor_context(league_slug, season, issue_key)
        return templates.TemplateResponse(request, "desk/editor.html", ctx)

    @app.get("/commissioner/{league_slug}/{season}/issue/{issue_key}/edit/full-preview")
    def full_preview(request: Request, league_slug: str, season: str, issue_key: str):
        import markdown as md

        league = get_league(league_slug)
        with storage() as s:
            assembled = assemble_issue(s, league, season, issue_key, week=_week_of(issue_key))
        sections = []
        for x in assembled["sections"]:
            if x["kind"] == "auto":
                continue
            html = (md.markdown(x["content_md"], extensions=["tables", "smarty"])
                    if x.get("content_md") else "<p><em>(no content yet)</em></p>")
            sections.append({"title": x["title"], "html": html, "approved": x["approved"],
                             "anchor": f"sec-{x['module_key']}"})
        return templates.TemplateResponse(request, "desk/full_preview.html", {
            "league": league, "season": season, "issue_key": issue_key,
            "theme": assembled.get("theme"), "sections": sections,
            "warnings": assembled["warnings"],
            "edit_base": f"/commissioner/{league_slug}/{season}/issue/{issue_key}/edit",
        })

    @app.get("/commissioner/{league_slug}/{season}/issue/{issue_key}/edit/preview-section")
    def preview_section(league_slug: str, season: str, issue_key: str, section: str):
        import markdown as md

        league = get_league(league_slug)
        path = _section_path(league, season, issue_key, section)
        text = _read(path)
        if text is None:
            return JSONResponse({"ok": False, "error": "no content"}, status_code=404)
        return JSONResponse({"ok": True,
                             "html": md.markdown(text, extensions=["tables", "smarty"])})

    # ------------------------------------------------------------ saving

    @app.post("/commissioner/{league_slug}/{season}/issue/{issue_key}/edit/save")
    async def editor_save(request: Request, league_slug: str, season: str, issue_key: str):
        body = await request.json()
        section = str(body.get("section") or "")
        text = str(body.get("text") or "").replace("\r\n", "\n")
        base_sha = str(body.get("base_sha") or "")
        chunk_index = body.get("chunk_index")
        league = get_league(league_slug)
        path = _section_path(league, season, issue_key, section)
        if path is None:
            return JSONResponse({"ok": False, "error": "unknown section"}, status_code=400)
        current = _read(path) or ""
        if chunk_index is None:
            if base_sha and base_sha != _sha(current):
                return JSONResponse({"ok": False, "error": "conflict"}, status_code=409)
            new_text = text
        else:
            chunks = _split_chunks(current)
            i = int(chunk_index)
            if i >= len(chunks) or int(body.get("chunk_count") or 0) != len(chunks):
                return JSONResponse({"ok": False, "error": "conflict"}, status_code=409)
            if base_sha and base_sha != _sha(chunks[i]):
                return JSONResponse({"ok": False, "error": "conflict"}, status_code=409)
            chunks[i] = text
            new_text = "".join(chunks)
        if new_text == current:
            return JSONResponse({"ok": True, "sha": _sha(text), "unchanged": True})
        path.parent.mkdir(parents=True, exist_ok=True)
        with storage() as s:
            if current:
                s.add_prose_revision(league_slug, season, issue_key, section,
                                     current, "commissioner-save")
            path.write_text(new_text, encoding="utf-8")
            s.set_prose_state(league_slug, season, issue_key, section, "commissioner-edited")
            if not section.startswith("matchup:"):
                # commissioner touched prose: approval must be re-asserted
                pass
        return JSONResponse({"ok": True, "sha": _sha(text),
                             "file_sha": _sha(new_text), "state": "commissioner-edited"})

    @app.post("/commissioner/{league_slug}/{season}/issue/{issue_key}/edit/approve")
    async def editor_approve(request: Request, league_slug: str, season: str, issue_key: str):
        body = await request.json()
        section = str(body.get("section") or "")
        action = str(body.get("action") or "")
        if action not in ("approve", "unapprove"):
            return JSONResponse({"ok": False, "error": "bad action"}, status_code=400)
        league = get_league(league_slug)
        m = _MATCHUP_RE.match(section)
        with storage() as s:
            if m:
                week = _week_of(issue_key)
                if week is None:
                    return JSONResponse({"ok": False, "error": "not a weekly issue"}, status_code=400)
                s.set_matchup_state(league_slug, season, week, m.group(1),
                                    status="approved" if action == "approve" else "edited")
                return JSONResponse({"ok": True, "approved": action == "approve"})
            if action == "approve":
                text = _read(_section_path(league, season, issue_key, section)) or ""
                bad = [mk for mk in BLOCKED_MARKERS if mk in text]
                if not text.strip():
                    return JSONResponse({"ok": False, "error": "section is empty"},
                                        status_code=400)
                if bad:
                    return JSONResponse(
                        {"ok": False, "error": f"blocked marker present: {bad[0]}"},
                        status_code=400)
            s.set_issue_module(league_slug=league_slug, season=season, issue_key=issue_key,
                               module_key=section, approved=1 if action == "approve" else 0)
        return JSONResponse({"ok": True, "approved": action == "approve"})

    @app.post("/commissioner/{league_slug}/{season}/issue/{issue_key}/edit/module")
    def editor_module(league_slug: str, season: str, issue_key: str,
                      module_key: str = Form(...), action: str = Form(...),
                      position: str = Form("")):
        fields: dict = {}
        if action == "include":
            fields["included"] = 1
        elif action == "exclude":
            fields["included"] = 0
        elif action == "move" and position.strip().lstrip("-").isdigit():
            fields["position"] = int(position)
        if fields:
            with storage() as s:
                s.set_issue_module(league_slug=league_slug, season=season,
                                   issue_key=issue_key, module_key=module_key, **fields)
        return RedirectResponse(
            f"/commissioner/{league_slug}/{season}/issue/{issue_key}/edit#sec-{module_key}",
            status_code=303)

    @app.post("/commissioner/{league_slug}/{season}/issue/{issue_key}/edit/rankings")
    async def editor_rankings(request: Request, league_slug: str, season: str, issue_key: str):
        form = await request.form()
        label = str(form.get("label") or ("preseason" if _week_of(issue_key) is None else issue_key))
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
            f"/commissioner/{league_slug}/{season}/issue/{issue_key}/edit#sec-power",
            status_code=303)

    # ------------------------------------------------- revisions / restore

    @app.get("/commissioner/{league_slug}/{season}/issue/{issue_key}/edit/revisions")
    def editor_revisions(league_slug: str, season: str, issue_key: str, section: str):
        with storage() as s:
            revs = s.get_prose_revisions(league_slug, season, issue_key, section, limit=15)
        return JSONResponse({"ok": True, "revisions": [
            {"id": r["id"], "created_at": r["created_at"], "source": r["source"],
             "preview": r["prior_text"][:160], "chars": len(r["prior_text"])}
            for r in revs]})

    @app.post("/commissioner/{league_slug}/{season}/issue/{issue_key}/edit/restore")
    async def editor_restore(request: Request, league_slug: str, season: str, issue_key: str):
        body = await request.json()
        section = str(body.get("section") or "")
        league = get_league(league_slug)
        path = _section_path(league, season, issue_key, section)
        if path is None:
            return JSONResponse({"ok": False, "error": "unknown section"}, status_code=400)
        with storage() as s:
            rev = s.get_prose_revision(int(body.get("revision_id") or 0))
            if not rev or (rev["league_slug"], rev["season"], rev["issue_key"], rev["section"]) \
                    != (league_slug, season, issue_key, section):
                return JSONResponse({"ok": False, "error": "unknown revision"}, status_code=404)
            current = _read(path) or ""
            if current:
                s.add_prose_revision(league_slug, season, issue_key, section, current, "restore")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(rev["prior_text"], encoding="utf-8")
            s.set_prose_state(league_slug, season, issue_key, section, "commissioner-edited")
        return JSONResponse({"ok": True})

    @app.post("/commissioner/{league_slug}/{season}/issue/{issue_key}/edit/reset-generated")
    async def editor_reset_generated(request: Request, league_slug: str, season: str, issue_key: str):
        """Lowdown only: replace lowdown.md with the generated rough draft."""
        body = await request.json()
        if str(body.get("section")) != "lowdown" or str(body.get("confirm")) != "yes":
            return JSONResponse({"ok": False, "error": "confirmation required"}, status_code=400)
        league = get_league(league_slug)
        idir = issue_dir(league, season, issue_key)
        rough = _read(idir / "lowdown" / "rough-lowdown.md")
        if rough is None:
            return JSONResponse({"ok": False, "error": "no generated draft exists"}, status_code=404)
        path = idir / "lowdown" / "lowdown.md"
        with storage() as s:
            current = _read(path) or ""
            if current:
                s.add_prose_revision(league_slug, season, issue_key, "lowdown", current, "restore")
            path.write_text(rough, encoding="utf-8")
            s.set_prose_state(league_slug, season, issue_key, "lowdown", "generated")
            s.set_issue_module(league_slug=league_slug, season=season, issue_key=issue_key,
                               module_key="lowdown", approved=0)
        return JSONResponse({"ok": True})

    # ------------------------------------------------ rewrite requests

    @app.post("/commissioner/{league_slug}/{season}/issue/{issue_key}/edit/request-rewrite")
    async def request_rewrite(request: Request, league_slug: str, season: str, issue_key: str):
        body = await request.json()
        section, note = str(body.get("section") or ""), str(body.get("note") or "").strip()
        if not note:
            return JSONResponse({"ok": False, "error": "note required"}, status_code=400)
        league = get_league(league_slug)
        if _section_path(league, season, issue_key, section) is None:
            return JSONResponse({"ok": False, "error": "unknown section"}, status_code=400)
        with storage() as s:
            rid = s.add_rewrite_request(league_slug, season, issue_key, section, note)
            _write_requests_file(s, league, season, issue_key)
        return JSONResponse({"ok": True, "request_id": rid})

    @app.post("/commissioner/{league_slug}/{season}/issue/{issue_key}/edit/proposal")
    async def proposal_action(request: Request, league_slug: str, season: str, issue_key: str):
        body = await request.json()
        section = str(body.get("section") or "")
        action = str(body.get("action") or "")
        league = get_league(league_slug)
        idir = issue_dir(league, season, issue_key)
        ppath = _proposal_path(idir, section)
        target = _section_path(league, season, issue_key, section)
        if target is None or not ppath.exists():
            return JSONResponse({"ok": False, "error": "no proposal"}, status_code=404)
        with storage() as s:
            if action == "accept":
                current = _read(target) or ""
                if current:
                    s.add_prose_revision(league_slug, season, issue_key, section,
                                         current, "proposal-accept")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(ppath.read_text(encoding="utf-8"), encoding="utf-8")
                s.set_prose_state(league_slug, season, issue_key, section, "commissioner-edited")
                if not section.startswith("matchup:"):
                    s.set_issue_module(league_slug=league_slug, season=season,
                                       issue_key=issue_key, module_key=section, approved=0)
            elif action != "discard":
                return JSONResponse({"ok": False, "error": "bad action"}, status_code=400)
            ppath.unlink()
            s.resolve_rewrite_requests(league_slug, season, issue_key, section,
                                       "done" if action == "accept" else "withdrawn")
            _write_requests_file(s, league, season, issue_key)
        return JSONResponse({"ok": True, "action": action})

    # ------------------------------------------------ publish / deploy

    @app.get("/commissioner/{league_slug}/{season}/issue/{issue_key}/edit/publish")
    def publish_confirm(request: Request, league_slug: str, season: str, issue_key: str):
        ctx = _editor_context(league_slug, season, issue_key)
        return templates.TemplateResponse(request, "desk/publish_confirm.html", ctx)

    def _do_publish(league_slug: str, season: str, issue_key: str):
        from leaguepage.publish import publish_assembled_issue

        league = get_league(league_slug)
        with storage() as s:
            return publish_assembled_issue(s, league, season, issue_key,
                                           week=_week_of(issue_key))

    @app.post("/commissioner/{league_slug}/{season}/issue/{issue_key}/edit/publish-local")
    def publish_local(request: Request, league_slug: str, season: str, issue_key: str,
                      confirm: str = Form("")):
        steps: list[dict] = []
        if confirm != "yes":
            steps.append({"name": "Confirmation", "ok": False, "detail": "not confirmed"})
            return templates.TemplateResponse(request, "desk/publish_result.html",
                                              _result_ctx(league_slug, season, issue_key, steps))
        steps.extend(_publish_and_build(league_slug, season, issue_key))
        return templates.TemplateResponse(request, "desk/publish_result.html",
                                          _result_ctx(league_slug, season, issue_key, steps))

    @app.post("/commissioner/{league_slug}/{season}/issue/{issue_key}/edit/publish-deploy")
    def publish_deploy(request: Request, league_slug: str, season: str, issue_key: str,
                       confirm: str = Form(""), confirm_deploy: str = Form("")):
        steps: list[dict] = []
        if confirm != "yes" or confirm_deploy != "yes":
            steps.append({"name": "Confirmation", "ok": False,
                          "detail": "Publish & Deploy needs both confirmations checked."})
            return templates.TemplateResponse(request, "desk/publish_result.html",
                                              _result_ctx(league_slug, season, issue_key, steps))
        steps.extend(_publish_and_build(league_slug, season, issue_key))
        if all(st["ok"] for st in steps):
            steps.extend(_deploy_production())
            if all(st["ok"] for st in steps):
                steps.append(_verify_production(league_slug, season, issue_key))
        return templates.TemplateResponse(request, "desk/publish_result.html",
                                          _result_ctx(league_slug, season, issue_key, steps))

    def _result_ctx(league_slug, season, issue_key, steps):
        return {"league": get_league(league_slug), "season": season, "issue_key": issue_key,
                "steps": steps, "all_ok": all(st["ok"] for st in steps),
                "production_url": PRODUCTION_URL,
                "issue_url": f"{PRODUCTION_URL}/{league_slug}/{season}/{issue_key}/",
                "edit_base": f"/commissioner/{league_slug}/{season}/issue/{issue_key}/edit"}

    def _publish_and_build(league_slug: str, season: str, issue_key: str) -> list[dict]:
        from leaguepage.publish import PublishError

        steps = []
        try:
            snap = _do_publish(league_slug, season, issue_key)
            steps.append({"name": "Publish snapshot", "ok": True, "detail": str(snap)})
        except (PublishError, ValueError) as exc:
            steps.append({"name": "Publish snapshot", "ok": False, "detail": str(exc)})
            return steps
        py = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
        proc = subprocess.run(
            [str(py if py.exists() else sys.executable), str(REPO_ROOT / "scripts" / "build_public_site.py")],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=300)
        detail = (proc.stdout or "").strip().splitlines()[-2:]
        steps.append({"name": "Public build + privacy audit", "ok": proc.returncode == 0,
                      "detail": " / ".join(detail) or (proc.stderr or "").strip()[-300:]})
        return steps

    def _deploy_production() -> list[dict]:
        steps = []
        npx = shutil.which("npx") or shutil.which("npx.cmd")
        if not npx:
            return [{"name": "Vercel deploy", "ok": False,
                     "detail": "npx not found on PATH; deploy from a terminal instead"}]
        try:
            link = subprocess.run([npx, "vercel@latest", "link", "--yes",
                                   "--project", VERCEL_PROJECT],
                                  cwd=DIST_DIR, capture_output=True, text=True, timeout=180)
            steps.append({"name": "Vercel link", "ok": link.returncode == 0,
                          "detail": (link.stderr or link.stdout or "").strip()[-200:]})
            if link.returncode != 0:
                return steps
            dep = subprocess.run([npx, "vercel@latest", "deploy", "--prod", "--yes"],
                                 cwd=DIST_DIR, capture_output=True, text=True, timeout=600)
            out = (dep.stdout or "") + (dep.stderr or "")
            steps.append({"name": "Vercel production deploy", "ok": dep.returncode == 0,
                          "detail": out.strip()[-300:]})
        except subprocess.TimeoutExpired:
            steps.append({"name": "Vercel deploy", "ok": False, "detail": "timed out"})
        return steps

    def _verify_production(league_slug: str, season: str, issue_key: str) -> dict:
        import urllib.request

        checks = []
        for path in ("/", f"/{league_slug}/", f"/{league_slug}/{season}/{issue_key}/"):
            try:
                with urllib.request.urlopen(PRODUCTION_URL + path, timeout=20) as resp:
                    checks.append((path, resp.status))
            except Exception:
                checks.append((path, 0))
        ok = all(code == 200 for _, code in checks)
        return {"name": "Production verification", "ok": ok,
                "detail": ", ".join(f"{p} -> {c or 'unreachable'}" for p, c in checks)}
