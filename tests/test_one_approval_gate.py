"""Approval is the one thing on this Desk that must have a single answer.

Three screens can approve a module: the long-form editor, the Lowdown page
and the issue builder. Only the first was asking whether the section could
be approved -- the builder set `approved=1` AND computed a signature over
the text, with no check at all. So the screen that applied none of the
rules produced a record indistinguishable from the screen that applied all
of them: a section still carrying a ROUGH DRAFT marker could be signed into
the publication record and read as audited afterwards.

The signature was never the gate. `issue_builder.approval_refusal` is, and
both screens ask it.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import leaguepage.config as cfg
import leaguepage.issue_builder as ib
import leaguepage.matchup_packet as mp
from leaguepage.config import get_league
from leaguepage.desk import create_app
from leaguepage.issue_builder import ROUGH_DRAFT_MARKER, approval_refusal
from leaguepage.storage import Storage

from fixtures import populate_league, populate_matchups, save_section

SEASON = "2027"
LG = get_league("surfeit")
BASE = f"/commissioner/surfeit/{SEASON}/issue/week-01"


@pytest.fixture
def env(tmp_path, monkeypatch):
    ed = tmp_path / "editorial"
    monkeypatch.setattr(ib, "EDITORIAL_DIR", ed)
    monkeypatch.setattr(mp, "EDITORIAL_DIR", ed)
    monkeypatch.setattr(cfg, "PUBLISHED_DIR", tmp_path / "published")
    monkeypatch.setattr(mp, "load_managers", lambda: {})
    monkeypatch.setattr(mp, "load_coalitions",
                        lambda: {"identities": {}, "coalitions": [], "relationships": []})
    db = tmp_path / "t.sqlite3"
    with Storage(db) as s:
        populate_league(s, LG, teams=10, rounds=3, picks="complete", season=SEASON)
        populate_matchups(s, LG, week=1, teams=10,
                          scores={rid: 90.0 + rid for rid in range(1, 11)})
        s.set_meta("current_week", "1")
    idir = ed / SEASON / "surfeit" / "week-01"
    (idir / "lowdown").mkdir(parents=True)
    (idir / "sections").mkdir()
    (idir / "proposals").mkdir()
    return TestClient(create_app(db_path=db)), db, idir


def _approved(db, module_key="lowdown"):
    with Storage(db) as s:
        row = s.get_issue_modules("surfeit", SEASON, "week-01").get(module_key) or {}
    return bool(row.get("approved")), row.get("approved_sha")


def _builder_approve(client, module_key="lowdown"):
    return client.post(f"{BASE}/builder/module",
                       data={"module_key": module_key, "action": "approve"},
                       follow_redirects=False)


# ------------------------------------------------------------- the rule

def test_an_empty_section_cannot_be_approved():
    assert approval_refusal("section", text="") == "section is empty"
    assert approval_refusal("lowdown", text="   \n ") == "section is empty"


def test_a_rough_draft_marker_cannot_be_approved():
    text = f"{ROUGH_DRAFT_MARKER}\n\nWords that are not finished yet.\n"
    assert "marker" in (approval_refusal("lowdown", text=text) or "")


def test_written_prose_is_approvable():
    assert approval_refusal("section", text="Real words, finished.\n") is None


def test_common_tactical_picture_is_gated_on_its_children_not_its_own_words():
    """It holds no prose of its own: it IS the week's previews."""
    assert approval_refusal("ctp", children=[], text="") \
        == "no matchups computed for this week"
    kids = [{"title": "A vs B", "written": False},
            {"title": "C vs D", "written": True}]
    assert "not written yet" in (approval_refusal("ctp", children=kids, text="") or "")
    written = [dict(k, written=True) for k in kids]
    assert approval_refusal("ctp", children=written, text="") is None


def test_a_kind_with_no_prose_of_its_own_is_not_gated_on_prose():
    """`power` is a blurb plus ranks; `auto` composes itself. Neither is
    refused for having no text, or nothing computed would ever approve."""
    assert approval_refusal("power", text="") is None
    assert approval_refusal("auto", text="") is None


# -------------------------------------------------- the screen that skipped it

def test_the_builder_refuses_an_empty_section(env):
    client, db, _idir = env
    r = _builder_approve(client)
    assert r.status_code == 303
    assert "refused=" in r.headers["location"]
    approved, sha = _approved(db)
    assert not approved, "an empty section was approved from the builder"
    assert not sha, "and signed"


def test_the_builder_refuses_a_marked_draft(env):
    client, db, _idir = env
    save_section(client, f"{BASE}/edit", "lowdown",
                 f"{ROUGH_DRAFT_MARKER}\n\nStill being written.\n")
    r = _builder_approve(client)
    assert "refused=" in r.headers["location"]
    approved, sha = _approved(db)
    assert not (approved or sha)


def test_the_builder_still_approves_what_the_editor_would(env):
    """The gate is not a ban. Finished prose approves, and it is signed."""
    client, db, _idir = env
    save_section(client, f"{BASE}/edit", "lowdown", "# The Lowdown\n\nFinished words.\n")
    r = _builder_approve(client)
    assert "refused=" not in r.headers["location"]
    approved, sha = _approved(db)
    assert approved and sha, "a clean section must still approve from here"


def test_a_refusal_is_shown_rather_than_swallowed(env):
    client, _db, _idir = env
    page = client.get(f"{BASE}/builder", params={"refused": "section is empty"})
    assert "Not approved" in page.text and "section is empty" in page.text


def test_both_screens_give_the_same_answer(env):
    """The point of moving the gate: one question, asked twice."""
    client, db, _idir = env
    save_section(client, f"{BASE}/edit", "lowdown",
                 f"{ROUGH_DRAFT_MARKER}\n\nStill being written.\n")

    editor = client.post(f"{BASE}/edit/approve",
                         json={"section": "lowdown", "action": "approve"})
    assert editor.status_code == 400
    from_editor = editor.json()["error"]

    builder = _builder_approve(client)
    assert "refused=" in builder.headers["location"]
    from_builder = builder.headers["location"].split("refused=", 1)[1]

    assert from_editor.split(":")[0] in from_builder.replace("%20", " ")
    assert not any(_approved(db))
