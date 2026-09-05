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

from leaguepage import pubqa
from leaguepage import takes as takes_mod
from leaguepage.config import DIST_DIR, REPO_ROOT, get_league
from leaguepage import prose, provenance, section_defaults
from leaguepage.issue_builder import (
    BLOCKED_MARKERS, BLURB_MODULES, CUSTOM_DEFAULT_TITLE, WRITING_SKILL,
    assemble_issue, _custom_index, is_custom_key, issue_dir,
    matchup_children, module_defs_for, module_states, next_custom_key,
)
from leaguepage.matchup_packet import ROUGH_DRAFT_MARKER, week_dir
from leaguepage.storage import utcnow_iso
from leaguepage.team_names import resolve_public_names

# Kinds whose file is public prose, and therefore his to change. A section
# he never opened is still one he can open: automation supplies the default,
# it does not take the pen away.
#
# `all-city` is here because a retired edition still publishes the copy that
# sits under its table, so that copy still needs an owner who is not a text
# editor pointed at the repository.
EDITABLE_KINDS = ("lowdown", "section", "all-city")


def _stale_key(league_slug: str, season: str, issue_key: str, section: str) -> str:
    """Where "this changed after you approved it" is remembered.

    A flag rather than a comparison: by the time the page renders, the text
    he approved is gone, so nothing on disk can still answer the question.
    It is set when an edit retires an approval and cleared the moment he
    makes an approval decision either way.
    """
    # Hyphen, not underscore: the prefix goes into a SQL LIKE, where an
    # underscore is a single-character wildcard.
    return f"approval-stale:{league_slug}:{season}:{issue_key}:{section}"


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


def _strip_draft_markers(text: str) -> str:
    """Remove the ROUGH DRAFT scaffolding comment lines from accepted prose.

    Only whole comment lines whose content is a blocked marker; a comment
    the author wrote for himself, and anything on a line with real words on
    it, is left exactly alone.
    """
    kept = []
    for line in text.splitlines():
        stripped = line.strip()
        if (stripped.startswith("<!--") and stripped.endswith("-->")
                and any(m in stripped for m in BLOCKED_MARKERS)):
            continue
        kept.append(line)
    return "\n".join(kept).lstrip("\n")


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

    def _authority(prov_row: dict | None, text: str, prose_state: str,
                   not_written: bool) -> str:
        """Who wrote what is on the page right now, in one line.

        Every branch is a stored fact. A recorded provenance hash says
        generated text was accepted here; whether it still matches says
        whether he has been through it since. Nothing is inferred from how
        the writing sounds, which is the whole reason the hash exists.
        """
        if not_written:
            return "Not written yet"
        line = provenance.desk_line(prov_row, text)
        if line == "Origin not recorded" and prose_state == "generated":
            # It arrived in the issue directory rather than through the
            # Desk, so nothing here knows who wrote it. Saying so beats
            # guessing in either direction.
            return "In the issue; no generator recorded, and no Desk edits since"
        return line

    def _ai_help_present(league, season: str, issue_key: str, section: str) -> bool:
        """An AI draft sits beside the box he writes in: a Claude proposal
        for the section, or the Lowdown's rough draft. Presence on the
        Desk at the moment he saves, nothing inferred later."""
        idir = issue_dir(league, season, issue_key)
        if _proposal_path(idir, section).exists():
            return True
        return section == "lowdown" and (idir / "lowdown" / "rough-lowdown.md").exists()

    def _record_origin_on_save(s, league, season: str, issue_key: str, section: str,
                               current: str) -> None:
        """Settle origin the first time the Desk writes a section.

        A file carrying the ROUGH DRAFT marker arrived under the Claude
        Code authoring contract, so the text before his first edit is the
        generated baseline. An empty section he writes into is his. Text
        of no known origin stays unknown: an edit to it proves nothing
        about who wrote the rest.
        """
        row = s.get_prose_provenance(league.slug, season, issue_key, section)
        method = "matchup-brief" if section.startswith("matchup:") else "section-brief"
        if provenance.origin_of(row) == "unknown":
            if current and ROUGH_DRAFT_MARKER in current:
                provenance.record(s, league_slug=league.slug, season=season,
                                  issue_key=issue_key, section=section,
                                  generator="claude-code", method=method,
                                  text=current, event="marker-arrival")
            elif not current.strip():
                provenance.mark_commissioner(s, league_slug=league.slug, season=season,
                                             issue_key=issue_key, section=section,
                                             method=method, event="commissioner-save")
        if _ai_help_present(league, season, issue_key, section):
            provenance.note_assistance(s, league_slug=league.slug, season=season,
                                       issue_key=issue_key, section=section,
                                       kind="ai-writing", method=method)

    def _mark_changed(s, league_slug: str, season: str, issue_key: str,
                      section: str, on: bool) -> None:
        s.set_meta(_stale_key(league_slug, season, issue_key, section),
                   utcnow_iso() if on else "")

    def _changed_since_approval(s, league_slug: str, season: str,
                                issue_key: str) -> dict[str, str]:
        """{section: when} for everything edited since it was signed off."""
        prefix = _stale_key(league_slug, season, issue_key, "")
        out = {}
        for section in _stale_sections(s, prefix):
            when = s.get_meta(prefix + section)
            if when:
                out[section] = when
        return out

    def _stale_sections(s, prefix: str) -> list[str]:
        rows = s._conn.execute(  # noqa: SLF001 - meta has no prefix scan
            "SELECT key FROM meta WHERE key LIKE ?", (prefix + "%",)).fetchall()
        return [r["key"][len(prefix):] for r in rows]

    def _invalidate_approval(s, league, season: str, issue_key: str,
                             section: str) -> None:
        """Changing published prose retires the sign-off it replaced.

        An approval is a statement about a particular text. Editing that
        text and leaving the approval standing publishes something nobody
        approved, which is the one failure this whole screen exists to
        prevent. So an edit takes the approval back and says why, and he
        re-approves what he now has.

        A matchup preview takes Common Tactical Picture with it. CTP has no
        text of its own: it publishes the previews, so signing it off was
        signing off exactly this writing.
        """
        m = _MATCHUP_RE.match(section)
        if m:
            week = _week_of(issue_key)
            if week is None:
                return
            st = s.get_matchup_state(league_slug=league.slug, season=season,
                                     week=week, matchup_slug=m.group(1)) or {}
            if (st.get("status") or "") in ("approved", "locked"):
                s.set_matchup_state(league_slug=league.slug, season=season,
                                    week=week, matchup_slug=m.group(1),
                                    status="edited")
                _mark_changed(s, league.slug, season, issue_key, section, True)
            if (s.get_issue_modules(league.slug, season, issue_key)
                    .get("ctp") or {}).get("approved"):
                s.set_issue_module(league_slug=league.slug, season=season,
                                   issue_key=issue_key, module_key="ctp",
                                   approved=0)
                _mark_changed(s, league.slug, season, issue_key, "ctp", True)
            return
        row = s.get_issue_modules(league.slug, season, issue_key).get(section) or {}
        if row.get("approved"):
            s.set_issue_module(league_slug=league.slug, season=season,
                               issue_key=issue_key, module_key=section,
                               approved=0)
            _mark_changed(s, league.slug, season, issue_key, section, True)

    def _blockers(s, league, season: str, issue_key: str, modules: list[dict]) -> list[dict]:
        assembled = assemble_issue(s, league, season, issue_key, week=_week_of(issue_key))
        titles = {m["module_key"]: m["title"] for m in modules}
        out = []
        # Each warning names its own module now. Matching on the displayed
        # title used to send two sections with the same name to the same
        # anchor, and pointed a custom section called "Fades" at the
        # standing Fades module's Exclude button.
        for row in assembled.get("warning_rows") or []:
            key, kind, text = row["module_key"], row["kind"], row["text"]
            # A warning about the issue as a whole belongs to no section.
            # It used to fall through to the team-name anchor, which sent
            # him to the wrong end of the page.
            anchor = (f"sec-{key}" if key
                      else ("blockers" if kind == "empty-issue" else "sec-team-names"))
            if kind == "empty-section":
                text = (f"'{titles.get(key, key)}' is included but not written "
                        f"yet — write it or exclude it from this issue.")
            out.append({"text": text, "anchor": anchor, "kind": kind,
                        "module_key": key})
        return out

    def _takes_rows(league_slug: str, season: str) -> list[dict]:
        """Tracked takes with the engine's last reading attached. A receipt
        is 'ready' when the engine has leaned one way and the Commissioner
        has not yet ruled."""
        with storage() as s:
            rows = s.all_takes(league_slug, season)
        out = []
        for t in rows:
            rec = t.get("recommended_status")
            out.append({
                **t,
                "status_label": takes_mod.STATUS_LABELS.get(t["status"], t["status"]),
                "recommended_label": takes_mod.STATUS_LABELS.get(rec) if rec else None,
                "why": t.get("resolution"),
                "receipt_ready": bool(
                    rec in (takes_mod.LEANING_RIGHT, takes_mod.LEANING_WRONG,
                            takes_mod.RESOLVED_RIGHT, takes_mod.RESOLVED_WRONG)
                    and rec != t["status"]),
            })
        out.sort(key=lambda t: (not t["receipt_ready"], t["take_id"]))
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
            qa = pubqa.check_issue(s, league, season, issue_key, week=week)
            open_requests = s.list_rewrite_requests(league_slug, season, issue_key)
            rev_counts = {sec: len(s.get_prose_revisions(league_slug, season, issue_key, sec, limit=50))
                          for sec in set(prose_states) | {m["module_key"] for m in modules}}
            label = "preseason" if week is None else issue_key
            rankings = s.get_power_rankings(league_slug, season, label)
            stale = _changed_since_approval(s, league_slug, season, issue_key)
            prov_rows = s.all_prose_provenance(league_slug, season, issue_key)
            # The computed half of Weekly Hardware: decided awards and the
            # basis each was decided on. Shown as evidence, and the source
            # the generated copy is composed from, so the page computes it
            # once rather than once per use.
            evidence_rows = {}
            if any(m["module_key"] == "hardware" for m in modules):
                evidence_rows["hardware"] = section_defaults.evidence_for(
                    s, league, season, issue_key, "hardware")

            def _brief(section: str) -> dict:
                b = brief_for_section(s, league, season, issue_key, section, week)
                written_at = (prose_rows.get(section) or {}).get("updated_at")
                # `data_as_of` is the leagues-table fetched_at, which moves on
                # every sync, so this chip lit up on every section every week
                # whether or not anything relevant had changed. It is a "we
                # synced" chip, not a "this is now wrong" chip. Compare the
                # brief's own content instead: if the research behind the
                # section is byte-identical to what it was when he wrote,
                # nothing he wrote has gone stale.
                b["stale_prose"] = False
                if written_at and b.get("data_as_of") and b["data_as_of"] > written_at:
                    digest = hashlib.sha1(
                        (b.get("text") or "").encode("utf-8")).hexdigest()[:16]
                    key = (f"brief_digest:{league_slug}:{season}:{issue_key}:"
                           f"{section}")
                    seen = s.get_meta(key)
                    b["stale_prose"] = bool(seen and seen != digest)
                    if seen != digest:
                        s.set_meta(key, digest)
                return b

            # The editing payload for each matchup preview. It hangs off the
            # Common Tactical Picture card below rather than living in a
            # second top-level list, because a preview is a piece of that
            # section and never a section of its own.
            matchup_editing = {}
            if week is not None:
                for child in next((m["children"] for m in modules
                                   if m["kind"] == "ctp"), []):
                    dpath = (week_dir(league, season, week) / "matchups"
                             / child["slug"] / "draft.md")
                    text = _read(dpath) or ""
                    st = s.get_matchup_state(league_slug=league_slug, season=season,
                                             week=week, matchup_slug=child["slug"]) or {}
                    section = child["section"]
                    matchup_editing[section] = {
                        **child,
                        "text": text, "sha": _sha(text),
                        "angle": st.get("custom_angle") or st.get("selected_angle_id") or "(no angle)",
                        "brief": _brief(section),
                        "proposal": _read(_proposal_path(idir, section)),
                        "revisions": len(s.get_prose_revisions(
                            league_slug, season, issue_key, section, limit=50)),
                    }
            briefs = {m["module_key"]: _brief(m["module_key"])
                      for m in modules
                      if m["kind"] in ("lowdown", "section", "power", "all-city")}
        requests_by_section: dict[str, list[dict]] = {}
        for r in open_requests:
            requests_by_section.setdefault(r["section"], []).append(r)
        for sec, me in matchup_editing.items():
            me["authority"] = _authority(prov_rows.get(sec), me["text"], "commissioner-edited",
                                         not me["text"].strip())
            me["origin"] = provenance.origin_of(prov_rows.get(sec))

        cards = []
        for m in modules:
            key, kind = m["module_key"], m["kind"]
            card = {**m, "anchor": f"sec-{key}", "editable": False,
                    "children": [{**matchup_editing[c["section"]],
                                   "requests": requests_by_section.get(c["section"], []),
                                   "changed_since_approval": bool(stale.get(c["section"]))}
                                  for c in m["children"] if c["section"] in matchup_editing],
                    "prose_state": prose_states.get(key, "generated"),
                    "requests": requests_by_section.get(key, []),
                    "revisions": rev_counts.get(key, 0), "proposal": None,
                    "blurb": None, "generated_available": False,
                    "evidence_rows": evidence_rows.get(key) or [],
                    "changed_since_approval": bool(stale.get(key)),
                    "brief": briefs.get(key)}
            if kind in EDITABLE_KINDS:
                path = _section_path(league, season, issue_key, key)
                text = _read(path)
                card["editable"] = True
                card["not_written"] = not (text or "").strip()
                text = text or ""
                card["file_sha"] = _sha(text)
                chunks = _split_chunks(text) if kind != "lowdown" else [text]
                card["chunks"] = [{"index": i, "text": c, "sha": _sha(c),
                                   "heading": _chunk_heading(c) if len(chunks) > 1 else None}
                                  for i, c in enumerate(chunks)]
                card["chunk_count"] = len(chunks)
                card["proposal"] = _read(_proposal_path(idir, key))
                card["authority"] = _authority(prov_rows.get(key), text,
                                               card["prose_state"],
                                               card["not_written"])
                card["origin"] = provenance.origin_of(prov_rows.get(key))
                if kind == "lowdown":
                    card["generated_source"] = _read(idir / "lowdown" / "rough-lowdown.md")
            elif key in BLURB_MODULES and not (kind == "ctp" and not m["children_total"]):
                # Its body is assembled by code; this is his voice on top of
                # it. Same file, same autosave, same history as any written
                # section, so the card offers the same controls and the
                # section keeps its own readiness rules.
                text = _read(_section_path(league, season, issue_key, key)) or ""
                card["blurb"] = {
                    "text": text, "sha": _sha(text),
                    "written": bool(text.strip()),
                    "proposal": _read(_proposal_path(idir, key)),
                }
            if key in section_defaults.GENERATED_DEFAULTS:
                card["generated_available"] = bool(
                    section_defaults.compose(key, evidence_rows.get(key) or []))
            if kind == "power":
                card["rankings"] = rankings
                card["label"] = "preseason" if week is None else issue_key
            cards.append(card)

        from leaguepage.team_analytics import player_values
        from leaguepage.team_names import identity_rows

        with storage() as s:
            values, _stage = player_values(s, league)
            name_rows = identity_rows(s, league, player_values=values)
        for r in name_rows:
            r["neutral"] = (r["public_name"] is None
                            or bool(re.fullmatch(r"Roster \d+", r["public_name"] or "")))
        base = f"/commissioner/{league_slug}/{season}/issue/{issue_key}"
        return {
            "league": league, "season": season, "issue_key": issue_key, "week": week,
            "cards": cards, "blockers": blockers,
            "qa": qa,
            "takes_rows": _takes_rows(league_slug, season),
            "take_topics": takes_mod.TOPICS,
            "take_statuses": [(k, takes_mod.STATUS_LABELS[k])
                              for k in takes_mod.STATUS_LABELS],
            "take_horizons": list(takes_mod.HORIZON_LABELS.items()),
            "base_takes": f"/commissioner/{league_slug}/{season}/take",
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
        league = get_league(league_slug)
        with storage() as s:
            assembled = assemble_issue(s, league, season, issue_key, week=_week_of(issue_key))
        sections = []
        for x in assembled["sections"]:
            if x["kind"] == "auto":
                continue
            html = (prose.render(x["content_md"])
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
        league = get_league(league_slug)
        path = _section_path(league, season, issue_key, section)
        text = _read(path)
        if text is None:
            return JSONResponse({"ok": False, "error": "no content"}, status_code=404)
        return JSONResponse({"ok": True, "html": prose.render(text)})

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
            _record_origin_on_save(s, league, season, issue_key, section, current)
            path.write_text(new_text, encoding="utf-8")
            s.set_prose_state(league_slug, season, issue_key, section, "commissioner-edited")
            # This used to be a comment and a `pass`. Approval survived every
            # edit, so a section could publish text nobody had signed off
            # while the Desk showed a green chip saying otherwise.
            _invalidate_approval(s, league, season, issue_key, section)
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
                # Keyword-only, like every other call site. Passing these
                # positionally raised TypeError inside the request and the
                # editor's Approve chip returned 500 for every matchup it
                # has ever been clicked on. Nothing to do with angles: the
                # call died before any angle or readiness logic ran.
                s.set_matchup_state(league_slug=league_slug, season=season, week=week,
                                    matchup_slug=m.group(1),
                                    status="approved" if action == "approve" else "edited")
                _mark_changed(s, league_slug, season, issue_key, section, False)
                return JSONResponse({"ok": True, "approved": action == "approve"})
            # Validated in both directions. Unapprove used to skip this
            # and write a row anyway, and `included` defaults to 1 in the
            # schema, so unapproving a retired key resurrected it --
            # included -- on an issue that had never carried it.
            kind = dict((k, kd) for k, _t, kd
                        in module_defs_for(
                            league, issue_key,
                            s.get_issue_modules(league_slug, season, issue_key))
                        ).get(section)
            if kind is None:
                return JSONResponse({"ok": False, "error": "unknown section"},
                                    status_code=404)
            # Common Tactical Picture holds no prose of its own: it is
            # made of the week's matchup previews, so the gate is that
            # they are all signed off. Checking it for a `ctp.md` that
            # never existed refused every click on it.
            if action == "approve" and kind == "ctp":
                week = _week_of(issue_key)
                kids = (matchup_children(s, league, season, issue_key, week)
                        if week is not None else [])
                left = [c for c in kids if not c["approved"]]
                if not kids:
                    return JSONResponse(
                        {"ok": False, "error": "no matchups computed for this week"},
                        status_code=400)
                if left:
                    return JSONResponse(
                        {"ok": False,
                         "error": f"{len(left)} matchup preview(s) still unapproved: "
                                  + ", ".join(c["title"] for c in left[:3])
                                  + ("…" if len(left) > 3 else "")},
                        status_code=400)
            elif action == "approve" and kind in ("lowdown", "section", "all-city"):
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
            # He has now ruled on what is actually there, either way.
            _mark_changed(s, league_slug, season, issue_key, section, False)
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
        elif action == "move":
            # `.lstrip("-")` accepted "--5", which then raised out of int()
            # as an unhandled 500.
            try:
                fields["position"] = int(position.strip())
            except (TypeError, ValueError):
                pass
        if fields:
            with storage() as s:
                s.set_issue_module(league_slug=league_slug, season=season,
                                   issue_key=issue_key, module_key=module_key, **fields)
        return RedirectResponse(
            f"/commissioner/{league_slug}/{season}/issue/{issue_key}/edit#sec-{module_key}",
            status_code=303)

    @app.post("/commissioner/{league_slug}/{season}/issue/{issue_key}/edit/custom")
    def editor_custom(league_slug: str, season: str, issue_key: str,
                      action: str = Form("add"), module_key: str = Form(""),
                      title: str = Form("")):
        """Create or rename one special section.

        A custom section exists because he made one, which is why nothing
        creates them in advance: an issue with no custom row has no custom
        section, and a new week does not invent an empty one to ignore. The
        row IS the section, so creating it is a single insert and the prose,
        preview, history and approval machinery all work on it unchanged.

        There is no delete. Excluding a section keeps its prose and takes it
        out of the paper, which is the recoverable version of the same
        intent; a button that could silently destroy writing is not worth
        the two clicks it saves.
        """
        with storage() as s:
            saved = s.get_issue_modules(league_slug, season, issue_key)
            if action == "add":
                key = next_custom_key(saved)
                # Numbered from the key it actually got. Counting existing
                # rows instead meant that reusing a freed key handed the new
                # section the wrong number and a duplicate position.
                n = _custom_index(key)
                s.set_issue_module(
                    league_slug=league_slug, season=season, issue_key=issue_key,
                    module_key=key, included=1, approved=0,
                    position=n,
                    custom_title=(title.strip() or f"{CUSTOM_DEFAULT_TITLE} {n}"))
            elif action == "rename" and is_custom_key(module_key):
                if module_key not in saved:
                    return JSONResponse({"ok": False, "error": "no such section"},
                                        status_code=404)
                s.set_issue_module(
                    league_slug=league_slug, season=season, issue_key=issue_key,
                    module_key=module_key,
                    custom_title=(title.strip() or CUSTOM_DEFAULT_TITLE))
                key = module_key
            else:
                return JSONResponse({"ok": False, "error": "bad action"},
                                    status_code=400)
        return RedirectResponse(
            f"/commissioner/{league_slug}/{season}/issue/{issue_key}/edit#sec-{key}",
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
            provenance.note_rankings(s, league_slug=league_slug, season=season,
                                     label=label, entries=entries)
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
            _invalidate_approval(s, league, season, issue_key, section)
        return JSONResponse({"ok": True})

    @app.post("/commissioner/{league_slug}/{season}/issue/{issue_key}/edit/reset-generated")
    async def editor_reset_generated(request: Request, league_slug: str, season: str, issue_key: str):
        """Put the generated version back, whatever generated it.

        Two kinds of generated version exist and they arrive differently.
        The Lowdown's is a Claude rough draft sitting in a file. Weekly
        Hardware's is composed from the week's decided awards at the moment
        it is asked for, because the awards are the source and a stale copy
        of them would be worse than none.

        His current text is snapshotted to History first either way. An
        override is never silently destroyed: it is one click away in the
        revision list for as long as the issue exists.

        Provenance follows the source. Text this code composed is
        deterministic in origin. A rough draft carrying the ROUGH DRAFT
        marker arrived under the Claude Code authoring contract and is AI in
        origin; a file without the marker has no known author and gets no
        claim.
        """
        body = await request.json()
        section = str(body.get("section") or "")
        if str(body.get("confirm")) != "yes":
            return JSONResponse({"ok": False, "error": "confirmation required"},
                                status_code=400)
        league = get_league(league_slug)
        idir = issue_dir(league, season, issue_key)
        path = _section_path(league, season, issue_key, section)
        if path is None:
            return JSONResponse({"ok": False, "error": "unknown section"}, status_code=400)
        composed = None
        with storage() as s:
            if section == "lowdown":
                generated = _read(idir / "lowdown" / "rough-lowdown.md")
            elif section in section_defaults.GENERATED_DEFAULTS:
                generated = composed = section_defaults.generated_md(
                    s, league, season, issue_key, section)
            else:
                return JSONResponse(
                    {"ok": False, "error": "this section has no generated version"},
                    status_code=400)
            if generated is None:
                return JSONResponse({"ok": False, "error": "no generated draft exists"},
                                    status_code=404)
            current = _read(path) or ""
            if current:
                s.add_prose_revision(league_slug, season, issue_key, section,
                                     current, "restore")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(generated, encoding="utf-8")
            s.set_prose_state(league_slug, season, issue_key, section, "generated")
            if composed is not None:
                provenance.record(
                    s, league_slug=league_slug, season=season, issue_key=issue_key,
                    section=section, generator=provenance.DETERMINISTIC,
                    method=section_defaults.GENERATED_METHOD.get(section),
                    text=composed, event="reset-generated")
            elif ROUGH_DRAFT_MARKER in generated:
                provenance.record(
                    s, league_slug=league_slug, season=season, issue_key=issue_key,
                    section=section, generator="claude-code", method="section-brief",
                    text=generated, event="reset-generated")
            _invalidate_approval(s, league, season, issue_key, section)
        return JSONResponse({"ok": True, "section": section})

    @app.post("/commissioner/{league_slug}/{season}/issue/{issue_key}/edit/replace-origin")
    async def editor_replace_origin(request: Request, league_slug: str, season: str,
                                    issue_key: str):
        """Replace with my copy: a deliberate change of authorship.

        The generated text goes to History and the box is cleared, so what
        he writes next is his in origin. The AI draft he is setting aside
        counts as assistance, because he read it. No similarity score can
        reach this state; only this click.
        """
        body = await request.json()
        section = str(body.get("section") or "")
        if str(body.get("confirm")) != "yes":
            return JSONResponse({"ok": False, "error": "confirmation required"},
                                status_code=400)
        league = get_league(league_slug)
        path = _section_path(league, season, issue_key, section)
        if path is None:
            return JSONResponse({"ok": False, "error": "unknown section"}, status_code=400)
        with storage() as s:
            row = s.get_prose_provenance(league_slug, season, issue_key, section)
            origin = provenance.origin_of(row)
            if origin not in ("ai", "deterministic"):
                return JSONResponse({"ok": False, "error": "this section is not "
                                     "generated in origin"}, status_code=400)
            current = _read(path) or ""
            if current:
                s.add_prose_revision(league_slug, season, issue_key, section,
                                     current, "replace-with-my-copy")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("", encoding="utf-8")
            provenance.mark_commissioner(
                s, league_slug=league_slug, season=season, issue_key=issue_key,
                section=section, assistance="ai-writing" if origin == "ai" else None,
                event="replace-with-my-copy")
            s.set_prose_state(league_slug, season, issue_key, section, "commissioner-edited")
            _invalidate_approval(s, league, season, issue_key, section)
        return JSONResponse({"ok": True, "section": section})

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

    @app.get("/commissioner/{league_slug}/{season}/issue/{issue_key}/edit/claude-prompt")
    def claude_prompt(league_slug: str, season: str, issue_key: str, section: str):
        """The text of a prompt to hand a Claude Code session, for the
        clipboard.

        Paths, not payload. Everything Claude needs to write this section is
        already on this machine in files it can open, so the prompt names
        them instead of pasting them. That keeps the research private
        by construction: nothing here leaves the Desk except instructions and
        file paths, and no private note, evidence line, ghost brief or
        manager identity travels in a clipboard.

        It writes to `proposals/`, never to his section file. His text stays
        authoritative until he accepts a proposal on the Desk.
        """
        league = get_league(league_slug)
        target = _section_path(league, season, issue_key, section)
        if target is None:
            return JSONResponse({"ok": False, "error": "unknown section"}, status_code=404)
        idir = issue_dir(league, season, issue_key)
        m = _MATCHUP_RE.match(section)
        with storage() as s:
            modules = {x["module_key"]: x for x in
                       module_states(s, league, season, issue_key, week=_week_of(issue_key))}
            title = None
            if m:
                for c in modules.get("ctp", {}).get("children", []):
                    if c["slug"] == m.group(1):
                        title = c["title"]
            elif section in modules:
                title = modules[section]["title"]
        # `_section_path` will happily build a path for any lowercase word,
        # so ask the issue what it actually contains before writing a prompt
        # to draft something that is not in it.
        if title is None:
            return JSONResponse({"ok": False, "error": "unknown section"}, status_code=404)
        def rel(pth: Path) -> str:
            try:
                return pth.relative_to(REPO_ROOT).as_posix()
            except ValueError:
                return pth.as_posix()
        research = (target.parent / "generated" / "AUTHORING.md" if m
                    else idir / "sections" / f"AUTHORING-{section}.md")
        lines = [
            f"Draft the {title} section for {league.display_name} "
            f"{season} {issue_key}.",
            "",
            f"1. Read `{WRITING_SKILL}` first and follow it. It is the voice",
            "   authority; nothing else overrides it.",
            f"2. Read `{rel(research)}` for the brief, the evidence and the",
            "   angles. Every fact in the draft comes from there — find the story,",
            "   never the numbers.",
            f"3. Write the full section to `{rel(_proposal_path(idir, section))}`,",
            f"   starting with `<!-- {ROUGH_DRAFT_MARKER} -->`.",
            f"4. Do not touch `{rel(target)}`. The Commissioner's text is",
            "   authoritative until he accepts the proposal on the Desk.",
            "",
            "Line breaks are honored on the published page, so break lines where",
            "you mean to and let paragraphs soft-wrap.",
        ]
        return JSONResponse({"ok": True, "section": section, "title": title,
                             "prompt": "\n".join(lines) + "\n"})

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
                # The draft marker is scaffolding, not prose: it exists so
                # unreviewed text cannot publish. Accepting IS the review,
                # so it comes off here rather than being left for him to
                # delete -- which would edit the text, break the hash, and
                # retire a provenance claim that was true. Left in, the
                # marker also blocks approval and publication outright, so
                # no accepted proposal could ever reach a page labelled.
                accepted = _strip_draft_markers(ppath.read_text(encoding="utf-8"))
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(accepted, encoding="utf-8")
                # Remember what was accepted, so the page can say so honestly
                # for as long as it is still exactly this. The moment he
                # edits a character the hash stops matching and the claim
                # retires itself; nothing has to notice or clean up.
                provenance.record(
                    s, league_slug=league_slug, season=season, issue_key=issue_key,
                    section=section, generator="claude-code",
                    method=("matchup-brief" if section.startswith("matchup:")
                            else "section-brief"),
                    text=accepted, event="proposal-accept")
                s.set_prose_state(league_slug, season, issue_key, section, "commissioner-edited")
                # Accepting replaces the section outright, so whatever was
                # approved before is gone. A matchup takes CTP's sign-off
                # with it, for the same reason an edit to one does.
                _invalidate_approval(s, league, season, issue_key, section)
            elif action == "discard":
                # He read it and kept his own. AI help reached the section
                # either way, and the origin of his text is unchanged.
                provenance.note_assistance(
                    s, league_slug=league_slug, season=season, issue_key=issue_key,
                    section=section, kind="ai-writing",
                    method=("matchup-brief" if section.startswith("matchup:")
                            else "section-brief"))
            else:
                return JSONResponse({"ok": False, "error": "bad action"}, status_code=400)
            ppath.unlink()
            s.resolve_rewrite_requests(league_slug, season, issue_key, section,
                                       "done" if action == "accept" else "withdrawn")
            _write_requests_file(s, league, season, issue_key)
        return JSONResponse({"ok": True, "action": action})

    @app.post("/commissioner/{league_slug}/{season}/issue/{issue_key}/edit/use-sleeper-name")
    def use_sleeper_name(league_slug: str, season: str, issue_key: str,
                         roster_id: str = Form(...)):
        """Drop the commissioner override so this roster follows its Sleeper
        team name automatically (per-row and explicit: never bulk-destroys
        deliberate overrides)."""
        from leaguepage.team_names import sleeper_team_names

        league = get_league(league_slug)
        with storage() as s:
            if roster_id.strip().isdigit() and sleeper_team_names(s, league).get(int(roster_id)):
                s.delete_public_team_name(league_slug, int(roster_id))
        return RedirectResponse(
            f"/commissioner/{league_slug}/{season}/issue/{issue_key}/edit#sec-team-names",
            status_code=303)

    # ------------------------------------------------------------ takes

    def _take_context(league, season: str) -> dict:
        """Everything take inference needs, computed once per request."""
        from leaguepage.pubqa import _norm_tokens
        from leaguepage.site_build import _team_slugs

        with storage() as s:
            names = resolve_public_names(s, league)
            public = {rid: v["name"] or f"Roster {rid}" for rid, v in names.items()}
            slugs = _team_slugs(s, league, names)
            players: dict[str, str] = {}
            for r in s.get_rosters(league.league_id):
                for pid in (r.get("players") or []):
                    p = s.get_player(pid) or {}
                    if p.get("full_name"):
                        players.setdefault(p["full_name"],
                                           (p.get("position") or "").upper())
            drafts = s.get_drafts_for_league(league.league_id)
            if drafts:
                for p in s.get_draft_picks(drafts[0]["draft_id"]):
                    meta = p.get("metadata") or {}
                    nm = " ".join(x for x in (meta.get("first_name"),
                                              meta.get("last_name")) if x).strip()
                    if nm:
                        players.setdefault(nm, (meta.get("position") or "").upper())
            settings = (s.get_league(league.league_id) or {}).get("settings") or {}
        return {"name_tokens": {rid: _norm_tokens(nm) for rid, nm in public.items()},
                "public_names": public, "slugs": slugs,
                "player_positions": players,
                "author_roster_id": league.author_roster_id,
                "playoff_week_start": settings.get("playoff_week_start")}

    @app.post("/commissioner/{league_slug}/{season}/issue/{issue_key}/edit/take")
    async def track_take(request: Request, league_slug: str, season: str, issue_key: str):
        """Track this take.

        Metadata the Commissioner left blank is inferred; anything he typed
        wins. `verbatim` records whether the quote is the published text
        unchanged — a paraphrase must never be presented to a reader as a
        quotation, and the public renderer reads this flag."""
        from leaguepage import takes as takes_mod

        body = await request.json()
        quote = str(body.get("quote") or "").strip()
        if not quote:
            return JSONResponse({"ok": False, "error": "a take needs a quote"},
                                status_code=400)
        league = get_league(league_slug)
        ctx = _take_context(league, season)
        section = str(body.get("section") or "lowdown")

        # Is this the published text, unchanged? Compare against the section
        # source rather than trusting a checkbox.
        source = _read(_section_path(league, season, issue_key, section)) or ""
        verbatim = bool(quote) and " ".join(quote.split()) in " ".join(source.split())

        subject_rid = body.get("subject_roster_id")
        subject_rid = int(subject_rid) if str(subject_rid or "").isdigit() else None
        if subject_rid is None:
            inferred = takes_mod.infer_subject(
                quote, name_tokens=ctx["name_tokens"],
                public_names=ctx["public_names"], slugs=ctx["slugs"])
            subject_rid = inferred.get("subject_roster_id")
            subject_type = inferred.get("subject_type") or "league"
            # A capsule sentence names its team in the heading above it, not
            # in the sentence. Without this a take tracked straight from the
            # rankings has no subject and can never be evaluated.
            if subject_rid is None and source:
                subject_rid = takes_mod.subject_from_heading(
                    quote, source, name_tokens=ctx["name_tokens"])
                if subject_rid is not None:
                    subject_type = "team"
        else:
            subject_type = "team"
        with storage() as s:
            take_id = takes_mod.create_take(
                s, league, season, quote=quote, issue_key=issue_key,
                section=section, week=_week_of(issue_key),
                topic=(body.get("topic") or None),
                subject_type=subject_type, subject_roster_id=subject_rid,
                subject=ctx["slugs"].get(subject_rid) if subject_rid else None,
                subject_name=ctx["public_names"].get(subject_rid) if subject_rid else None,
                confidence=(body.get("confidence") or None),
                review_after=(body.get("review_after") or None),
                verbatim=verbatim,
                # land the reader on the section the claim came from
                href=f"{season}/{issue_key}/index.html#{section}",
                note=(body.get("note") or None),
                players=takes_mod.infer_players(quote, ctx["player_positions"]),
                playoff_week_start=ctx["playoff_week_start"])
        return JSONResponse({"ok": True, "take_id": take_id, "verbatim": verbatim})

    @app.get("/commissioner/{league_slug}/{season}/issue/{issue_key}/edit/take-candidates")
    def take_candidates(league_slug: str, season: str, issue_key: str):
        """Possible takes from this issue. Offers only; creates nothing."""
        import json as _json

        from leaguepage import takes as takes_mod
        from leaguepage.config import PUBLISHED_DIR
        from leaguepage.publish import snapshot_family

        league = get_league(league_slug)
        ctx = _take_context(league, season)
        family = snapshot_family(PUBLISHED_DIR, league_slug, season, issue_key)
        if family:
            snap = _json.loads(family[-1].read_text(encoding="utf-8"))
            snap["issue_key"] = issue_key
            snap.setdefault("href", f"{season}/{issue_key}/index.html")
        else:
            # Not published yet: offer candidates from the live workspace, so
            # a take can be tracked while the issue is still being written.
            with storage() as s:
                a = assemble_issue(s, league, season, issue_key,
                                   week=_week_of(issue_key))
            snap = {"issue_key": issue_key, "issue_label": issue_key,
                    "href": f"{season}/{issue_key}/index.html",
                    "sections": [{"module_key": m["module_key"], "title": m["title"],
                                  "content_md": m.get("content_md") or ""}
                                 for m in a["sections"] if m.get("content_md")]}
        with storage() as s:
            tracked = {t["quote"] for t in s.all_takes(league_slug, season)}
        cands = takes_mod.candidate_takes(
            snap, existing_quotes=tracked,
            **{k: ctx[k] for k in ("name_tokens", "public_names", "slugs",
                                   "player_positions", "author_roster_id")})
        return JSONResponse({"candidates": cands})

    @app.post("/commissioner/{league_slug}/{season}/take/{take_id}")
    async def take_action(request: Request, league_slug: str, season: str,
                          take_id: int):
        """Commissioner verdict. The engine's recommendation is untouched by
        this, so a disagreement stays visible in the ledger."""
        from leaguepage.storage import Storage as _S

        body = await request.json()
        action = str(body.get("action") or "")
        with storage() as s:
            take = s.get_take(take_id)
            if not take or take["league_slug"] != league_slug:
                return JSONResponse({"ok": False, "error": "unknown take"},
                                    status_code=404)
            if action == "status":
                status = str(body.get("status") or "")
                if status not in _S.TAKE_STATUSES:
                    return JSONResponse({"ok": False, "error": "bad status"},
                                        status_code=400)
                s.set_take_status(take_id, status, body.get("resolution") or None)
            elif action == "public":
                s.set_take_public(take_id, bool(body.get("public")))
            elif action == "delete":
                s.delete_take(take_id)
            else:
                return JSONResponse({"ok": False, "error": "bad action"},
                                    status_code=400)
        return JSONResponse({"ok": True})

    # -------------------------------------------- publication check panel

    @app.post("/commissioner/{league_slug}/{season}/issue/{issue_key}/edit/qa-action")
    async def qa_action(request: Request, league_slug: str, season: str, issue_key: str):
        """Accept / Ignore one proofread suggestion.

        Accept is a LITERAL replacement of the exact text the panel showed —
        never a regenerated sentence, never a style edit. The prior text is
        snapshotted into revision history first, so the Commissioner can
        restore it from the section's own History like any other edit."""
        body = await request.json()
        action = str(body.get("action") or "")
        finding_id = str(body.get("finding_id") or "")
        league = get_league(league_slug)
        week = _week_of(issue_key)
        if action in ("ignore", "unignore"):
            with storage() as s:
                fn = pubqa.ignore_finding if action == "ignore" else pubqa.unignore_finding
                fn(s, league_slug, season, issue_key, finding_id)
            return JSONResponse({"ok": True})
        if action != "accept":
            return JSONResponse({"ok": False, "error": "bad action"}, status_code=400)

        with storage() as s:
            rep = pubqa.check_issue(s, league, season, issue_key, week=week)
            found = next((f for g in rep["groups"] for f in g["findings"]
                          if f["finding_id"] == finding_id), None)
            if not found:
                return JSONResponse({"ok": False, "error": "finding no longer present"},
                                    status_code=409)
            if not found.get("fix_from") or not found.get("fix_to"):
                return JSONResponse({"ok": False, "error": "no mechanical fix for this finding"},
                                    status_code=400)
            path = _section_path(league, season, issue_key, found["module_key"] or "")
            current = _read(path) if path else None
            if current is None:
                return JSONResponse({"ok": False, "error": "section not found"},
                                    status_code=404)
            if current.count(found["fix_from"]) != 1:
                return JSONResponse(
                    {"ok": False, "error": "the text moved since the check ran; "
                                           "reload and try again"}, status_code=409)
            s.add_prose_revision(league_slug, season, issue_key, found["module_key"],
                                 current, "qa-suggestion-accepted")
            path.write_text(current.replace(found["fix_from"], found["fix_to"], 1),
                            encoding="utf-8")
            s.set_prose_state(league_slug, season, issue_key, found["module_key"],
                              "commissioner-edited")
        return JSONResponse({"ok": True})

    @app.get("/commissioner/{league_slug}/{season}/issue/{issue_key}/edit/qa")
    def qa_panel(request: Request, league_slug: str, season: str, issue_key: str):
        league = get_league(league_slug)
        with storage() as s:
            rep = pubqa.check_issue(s, league, season, issue_key,
                                    week=_week_of(issue_key))
        return JSONResponse(rep)

    # ------------------------------------------------ publish / deploy


    @app.get("/commissioner/{league_slug}/{season}/issue/{issue_key}/edit/publish")
    def publish_confirm(request: Request, league_slug: str, season: str, issue_key: str):
        from leaguepage import publish_jobs

        ctx = _editor_context(league_slug, season, issue_key)
        ctx["job"] = publish_jobs.get_job_for(league_slug, season, issue_key)
        with storage() as s:
            ctx["deploy_state"] = publish_jobs.deploy_state(s, league_slug, season, issue_key)
            ctx["published_rev"], ctx["text_changed"] = _publication_state(
                s, league_slug, season, issue_key)
            ctx["last_change"] = publish_jobs.last_public_change(s, league_slug)
        ds, rev = ctx["deploy_state"], ctx["published_rev"]
        # Is what production carries the latest frozen revision?
        ctx["live"] = bool(ds and ds.get("state") in publish_jobs.LIVE_STATES and rev
                           and ds.get("revision") == rev["n"])
        ctx["live_ago"] = publish_jobs.ago(ds["at"]) if ds and ds.get("at") else None
        return templates.TemplateResponse(request, "desk/publish_confirm.html", ctx)

    def _publication_state(s, league_slug: str, season: str, issue_key: str):
        """(latest frozen revision or None, whether the Desk text differs
        from it). Tells the page whether the next publish is a republish,
        a no-op, or a correction that needs a note."""
        import json as _json

        from leaguepage import config as cfg
        from leaguepage.publish import snapshot_family, text_changed_since_publish

        family = snapshot_family(cfg.PUBLISHED_DIR, league_slug, season, issue_key)
        if not family:
            return None, None
        latest = _json.loads(family[-1].read_text(encoding="utf-8"))
        rev = {"n": int(latest.get("revision") or 1),
               "at": latest.get("revised_at") or latest.get("published_at") or "",
               "count": len(family), "note": latest.get("revision_note")}
        try:
            changed = text_changed_since_publish(
                s, get_league(league_slug), season, issue_key, week=_week_of(issue_key))
        except Exception:
            changed = None
        return rev, changed

    @app.post("/commissioner/{league_slug}/{season}/issue/{issue_key}/edit/publish-start")
    def publish_start(league_slug: str, season: str, issue_key: str,
                      mode: str = Form(...), confirm: str = Form(""),
                      confirm_deploy: str = Form(""), note: str = Form("")):
        from leaguepage import publish_jobs

        back = f"/commissioner/{league_slug}/{season}/issue/{issue_key}/edit/publish"
        if mode not in ("local", "deploy") or confirm != "yes"                 or (mode == "deploy" and confirm_deploy != "yes"):
            return RedirectResponse(back + "?error=confirm", status_code=303)
        note = note.strip()
        with storage() as s:
            _rev, changed = _publication_state(s, league_slug, season, issue_key)
        if changed and not note:
            # Refuse before a job exists: the snapshot stage would refuse
            # anyway, but a form field is a better place to learn that.
            return RedirectResponse(back + "?error=note", status_code=303)
        publish_jobs.start_publish_job(db_path_of(), league_slug, season, issue_key, mode,
                                       note=note or None)
        return RedirectResponse(back, status_code=303)

    def db_path_of():
        # the desk's storage factory closes over its db_path; jobs need it
        with storage() as s:
            return s.db_path

    @app.get("/commissioner/{league_slug}/{season}/issue/{issue_key}/edit/publish-status")
    def publish_status(league_slug: str, season: str, issue_key: str):
        from leaguepage import publish_jobs

        job = publish_jobs.get_job_for(league_slug, season, issue_key)
        with storage() as s:
            dstate = publish_jobs.deploy_state(s, league_slug, season, issue_key)
        payload: dict = {"deploy_state": dstate}
        if job:
            payload["job"] = {k: job[k] for k in
                              ("job_id", "mode", "state", "created_at", "ended_at",
                               "stages", "production_url", "issue_url", "deployment_id")}
            if job["state"] == "failed":
                try:
                    payload["log_tail"] = Path(job["log_path"]).read_text(
                        encoding="utf-8")[-3000:]
                except OSError:
                    payload["log_tail"] = "(log unavailable)"
        return JSONResponse(payload)
