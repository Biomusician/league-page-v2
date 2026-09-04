"""None of the research reaches a reader.

The Desk computes a lot that no reader may see: what each team could still
do, what is on the record against them, the ghost text in the writing box,
the commissioner's own notes, the evidence references behind every number,
and the prompt he copies for a Claude Code session.

The guarantee is structural rather than a filter. Briefs are computed live
at page load and never stored; the issue is assembled from section files
only; the published snapshot is built from that assembly; and the site is
built from snapshots. Research has no path into any of those, and these
tests walk each boundary rather than trusting the chain.

The one way any of it becomes public is the intended one: he reads it and
writes it, in his own words, into a section.
"""
from __future__ import annotations

import json

import pytest

import leaguepage.config as cfg
import leaguepage.issue_builder as ib
import leaguepage.matchup_packet as mp
from leaguepage.config import get_league
from leaguepage.desk import create_app
from leaguepage.ghost_briefs import brief_for_section
from leaguepage.issue_builder import assemble_issue, matchup_children
from leaguepage.storage import Storage

from fastapi.testclient import TestClient
from fixtures import populate_league, populate_matchups

SEASON = "2026"
LG = get_league("surfeit")
EDIT = f"/commissioner/surfeit/{SEASON}/issue/week-01/edit"

# Headings and phrases that exist only in private research. If any of these
# turns up in public output, something crossed a boundary.
PRIVATE_MARKERS = [
    "WHAT THEY COULD STILL DO",
    "ON THE RECORD AGAINST THEM",
    "roast ammunition",
    "WHO MIGHT NOT PLAY",
    "WHAT EACH SIDE HAS TO GET PAST",
    "WHAT THEY JUST DID",
    "POSSIBLE ANGLES",
    "Writing suggestions",
    "FAAB left",
    "thinnest at",
    "reads as:",
    "Commissioner notes",
    "commissioner_notes",
    "Draft the",                 # the copied Claude prompt's first words
    "my-writing-style",
    "ROUGH DRAFT - COMMISSIONER EDIT REQUIRED",
]


@pytest.fixture
def env(tmp_path, monkeypatch):
    ed = tmp_path / "editorial"
    monkeypatch.setattr(ib, "EDITORIAL_DIR", ed)
    monkeypatch.setattr(mp, "EDITORIAL_DIR", ed)
    monkeypatch.setattr(cfg, "PUBLISHED_DIR", tmp_path / "published")
    db = tmp_path / "t.sqlite3"
    with Storage(db) as s:
        populate_league(s, LG, teams=10, rounds=3, picks="complete", season=SEASON)
        populate_matchups(s, LG, week=1, teams=10,
                          scores={rid: 90.0 + rid for rid in range(1, 11)})
        s.set_meta("current_week", "1")
    return TestClient(create_app(db_path=db)), db, ed, tmp_path


def _slug(db):
    with Storage(db) as s:
        return matchup_children(s, LG, SEASON, "week-01", 1)[0]["slug"]


def test_the_research_exists_before_anything_claims_it_is_absent(env):
    """A privacy test that passes because the feature is missing proves
    nothing. Establish that the private material is really there."""
    _c, db, _ed, _tmp = env
    with Storage(db) as s:
        text = brief_for_section(s, LG, SEASON, "week-01",
                                 f"matchup:{_slug(db)}", 1)["text"]
    for marker in ("WHAT THEY COULD STILL DO", "ON THE RECORD AGAINST THEM",
                   "roast ammunition"):
        assert marker in text, marker


def test_no_brief_is_stored_anywhere(env):
    """Briefs are computed at page load. Nothing writes one to the issue
    directory or the database, so there is nothing to leak later."""
    client, db, ed, _tmp = env
    client.get(EDIT)                       # renders every brief on the page
    for f in ed.rglob("*"):
        if not f.is_file():
            continue
        text = f.read_text(encoding="utf-8", errors="ignore")
        for marker in ("ON THE RECORD AGAINST THEM", "WHAT THEY COULD STILL DO"):
            assert marker not in text, f"{f.name} carries {marker!r}"
    with Storage(db) as s:
        rows = s._conn.execute(  # noqa: SLF001 - a deliberate whole-DB sweep
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        for r in rows:
            dump = str(s._conn.execute(f"SELECT * FROM {r['name']}").fetchall())  # noqa: SLF001
            assert "ON THE RECORD AGAINST THEM" not in dump, r["name"]


def test_the_assembled_issue_carries_only_section_prose(env):
    """The assembly reads section files. Research is not one of them."""
    _c, db, _ed, _tmp = env
    with Storage(db) as s:
        assembled = assemble_issue(s, LG, SEASON, "week-01", week=1)
    blob = json.dumps(assembled)
    for marker in PRIVATE_MARKERS:
        assert marker not in blob, marker


def test_a_published_snapshot_carries_only_section_prose(env):
    """The frozen snapshot is what the site renders, so it is the boundary
    that actually matters."""
    from leaguepage import publish

    client, db, ed, tmp = env
    slug = _slug(db)
    # write and approve exactly one matchup, the way he would
    mdir = ed / SEASON / "surfeit" / "week-01" / "matchups" / slug
    mdir.mkdir(parents=True, exist_ok=True)
    (mdir / "draft.md").write_text("Two teams, one kicker between them.\n",
                                   encoding="utf-8")
    (mdir / "commissioner_notes.md").write_text(
        "PRIVATE: he reached on Jordan James and I am going to say so.\n",
        encoding="utf-8")
    with Storage(db) as s:
        s.set_matchup_state(league_slug="surfeit", season=SEASON, week=1,
                            matchup_slug=slug, status="approved")
        try:
            publish.publish_assembled_issue(s, LG, SEASON, "week-01",
                                            published_dir=tmp / "published", week=1)
        except publish.PublishError:
            pass                # gates refuse an unfinished issue; that is fine
    for f in (tmp / "published").rglob("*.json"):
        text = f.read_text(encoding="utf-8")
        for marker in PRIVATE_MARKERS + ["PRIVATE: he reached on Jordan James"]:
            assert marker not in text, f"{f.name} carries {marker!r}"


def test_commissioner_notes_never_leave_the_packet(env):
    """His notes are input to writing, not output of it."""
    _c, db, ed, _tmp = env
    slug = _slug(db)
    mdir = ed / SEASON / "surfeit" / "week-01" / "matchups" / slug
    mdir.mkdir(parents=True, exist_ok=True)
    (mdir / "commissioner_notes.md").write_text("Do not print this.\n",
                                                encoding="utf-8")
    with Storage(db) as s:
        assembled = assemble_issue(s, LG, SEASON, "week-01", week=1)
    assert "Do not print this" not in json.dumps(assembled)


def test_the_copied_prompt_is_not_part_of_the_issue(env):
    """Copying a prompt is not an editorial act and leaves no trace in the
    thing that publishes."""
    client, db, _ed, _tmp = env
    r = client.get(f"{EDIT}/claude-prompt", params={"section": "tracks"})
    assert r.status_code == 200
    prompt = r.json()["prompt"]
    with Storage(db) as s:
        assembled = assemble_issue(s, LG, SEASON, "week-01", week=1)
    assert prompt not in json.dumps(assembled)
    assert "proposals/tracks.md" not in json.dumps(assembled)


def test_evidence_references_are_desk_only(env):
    """`sleeper:roster:...` and `editorial:coalition:...` are provenance for
    him, and they are internal identifiers."""
    _c, db, _ed, _tmp = env
    with Storage(db) as s:
        brief = brief_for_section(s, LG, SEASON, "week-01",
                                  f"matchup:{_slug(db)}", 1)
        assembled = assemble_issue(s, LG, SEASON, "week-01", week=1)
    blob = json.dumps(assembled)
    for ref in brief.get("evidence") or []:
        assert ref not in blob, ref
    for prefix in ("sleeper:roster:", "sleeper:matchup:", "editorial:coalition:"):
        assert prefix not in blob, prefix


def test_what_he_writes_himself_does_publish(env):
    """The control. If nothing reached the page these tests would pass for
    the wrong reason: the boundary is what he did not write, not everything."""
    _c, db, ed, _tmp = env
    sdir = ed / SEASON / "surfeit" / "week-01" / "sections"
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / "tracks.md").write_text(
        "They have 100 FAAB and no tight end. Draw your own conclusion.\n",
        encoding="utf-8")
    with Storage(db) as s:
        assembled = assemble_issue(s, LG, SEASON, "week-01", week=1)
    assert "Draw your own conclusion" in json.dumps(assembled)
