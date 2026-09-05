"""The Issue Room: a week's work in one place.

The acceptance test for the shell, expressed as the Commissioner's own
journey — open the issue, move between sections without a page load, see
what needs him, read the research, look at the real page, reach Publish.

The room shares the long-form editor's context, section card and endpoints
on purpose. These tests pin the sharing, because two surfaces that drift
into disagreeing about what a section is would be worse than the one long
column this replaces.
"""
from __future__ import annotations

import pathlib
import re

import pytest
from fastapi.testclient import TestClient

import leaguepage.config as cfg
import leaguepage.issue_builder as ib
import leaguepage.matchup_packet as mp
from leaguepage.config import get_league
from leaguepage.desk import create_app
from leaguepage.desk_editor import _rail_state
from leaguepage.storage import Storage

from fixtures import populate_league, populate_matchups

SEASON = "2027"
LG = get_league("surfeit")
BASE = f"/commissioner/surfeit/{SEASON}/issue/week-01"
ROOM = f"{BASE}/room"


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
    (idir / "lowdown" / "lowdown.md").write_text("# The Lowdown\n\nOriginal words.\n",
                                                 encoding="utf-8")
    (idir / "sections").mkdir()
    (idir / "proposals").mkdir()
    return TestClient(create_app(db_path=db)), db, idir


ATTR = r'data-{}="([a-z0-9:_-]+)"'          # the attribute, never a JS template literal


def _room(client):
    r = client.get(ROOM)
    assert r.status_code == 200, r.text[-800:]
    return r.text


def _rail(html):
    return html[html.index('aria-label="Sections in this issue"'):html.index("</nav>")]


def _make_ready(client, db):
    """One approved section and nothing else included: the smallest issue
    that passes every publication gate."""
    with Storage(db) as s:
        for key in ("hardware", "ctp", "power", "tracks", "fades", "forceflow",
                    "blackbox", "false-assumptions", "branches", "draft-capsules", "custom"):
            s.set_issue_module(league_slug="surfeit", season=SEASON, issue_key="week-01",
                               module_key=key, included=0)
    r = client.post(f"{BASE}/edit/approve", json={"section": "lowdown", "action": "approve"})
    assert r.status_code == 200, r.text


# ------------------------------------------------- one issue, one page

def test_every_section_is_in_the_room_and_only_one_is_shown(env):
    """Moving between sections costs no request, so an unsaved box is
    never lost to navigation."""
    client, _db, _idir = env
    html = _room(client)
    panes = re.findall(ATTR.format("pane"), html)
    rails = re.findall(ATTR.format("rail"), html)
    assert set(panes) == set(rails) and len(panes) >= 8
    for key in ("lowdown", "ctp", "hardware"):
        assert key in panes, key
    # exactly one pane starts visible; the rest carry `hidden`
    opens = re.findall(r'<div class="pane" data-pane="[a-z0-9:_-]+"([^>]*)>', html)
    assert len(opens) == len(panes)
    assert sum(1 for o in opens if "hidden" not in o) == 1, opens
    # no anchor navigates to another editing page for a section
    assert f'href="{BASE}/edit#' not in html


def test_the_room_runs_the_paper_in_the_paper_order(env):
    client, _db, _idir = env
    html = _room(client)
    rail = _rail(html)
    weekly = rail[:rail.index("Administration")] if "Administration" in rail else rail
    order = re.findall(ATTR.format("rail"), weekly)
    assert order[0] == "lowdown", order
    assert order.index("ctp") < order.index("hardware")
    assert order[-1] == "hardware", "Weekly Hardware closes every issue"


def test_the_rail_says_what_needs_him_not_what_the_column_is_called(env):
    """Implementation words are not the visual language of the rail."""
    client, _db, idir = env
    html = _room(client)
    labels = set(re.findall(r'class="tok tok-\w+">([^<]+)<', _rail(html)))
    assert labels, "the rail carries state tokens"
    for jargon in ("commissioner-edited", "generated", "not_written", "prose_state"):
        assert jargon not in labels, jargon
    assert labels <= {"excluded", "automatic", "AI draft ready", "needs writing",
                      "needs review", "approved", "nothing this week"} | {
        l for l in labels if re.fullmatch(r"\d+/\d+ written", l)}, labels


@pytest.mark.parametrize("card,expected", [
    ({"included": False}, ("excluded", "off")),
    ({"included": True, "kind": "auto"}, ("automatic", "off")),
    ({"included": True, "proposal": "text"}, ("AI draft ready", "ai")),
    ({"included": True, "editable": True, "not_written": True}, ("needs writing", "work")),
    ({"included": True, "changed_since_approval": True}, ("needs review", "need")),
    ({"included": True, "empty": True}, ("nothing this week", "work")),
    ({"included": True, "approved": True}, ("approved", "ok")),
    ({"included": True, "children_total": 6, "children_written": 2}, ("2/6 written", "work")),
    ({"included": True, "children_total": 6}, ("0/6 written", "work")),
    ({"included": True, "children_total": 6, "children_written": 6, "approved": True},
     ("approved", "ok")),
])
def test_rail_state_precedence(card, expected):
    assert _rail_state(card) == expected


# ------------------------------------------------- shared implementation

def test_the_room_and_the_editor_are_one_implementation(env):
    """Same card partial, same script, same endpoints. A second copy of
    any of those is how the two surfaces start disagreeing."""
    room = pathlib.Path("templates/desk/issue_room.html").read_text(encoding="utf-8")
    editor = pathlib.Path("templates/desk/editor.html").read_text(encoding="utf-8")
    for tpl in (room, editor):
        assert '{% include "desk/_section_card.html" %}' in tpl
        assert '<script src="/static/desk-editor.js"></script>' in tpl
    # the behaviour lives in one file, not two inline blocks
    assert "async function saveOne" not in room and "async function saveOne" not in editor
    js = pathlib.Path("static/desk-editor.js").read_text(encoding="utf-8")
    for fn in ("saveAll", "approve", "proposal", "showRevisions", "replaceOrigin"):
        assert f"function {fn}(" in js, fn


def test_the_room_writes_through_the_same_endpoints(env):
    """Edit in the room, and the section's history, provenance and
    approval behave exactly as they do from the editor."""
    client, db, idir = env
    _room(client)
    r = client.post(f"{BASE}/edit/save",
                    json={"section": "lowdown", "text": "Rewritten in the room.\n",
                          "base_sha": ""})
    assert r.status_code == 200, r.text
    assert (idir / "lowdown" / "lowdown.md").read_text(encoding="utf-8") == "Rewritten in the room.\n"
    revs = client.get(f"{BASE}/edit/revisions", params={"section": "lowdown"}).json()
    assert revs["revisions"], "history still records what was replaced"
    assert "Rewritten in the room." in _room(client)


def test_a_stale_box_in_a_second_tab_cannot_overwrite_newer_prose(env):
    """Two devices, one Commissioner. The room uses the same conflict
    check the editor does, so last-write-wins never happens silently."""
    client, _db, _idir = env
    client.post(f"{BASE}/edit/save",
                json={"section": "lowdown", "text": "First.\n", "base_sha": ""})
    stale = client.post(f"{BASE}/edit/save",
                        json={"section": "lowdown", "text": "From the other tab.\n",
                              "base_sha": "0" * 64})
    assert stale.status_code == 409 and stale.json()["error"] == "conflict"


# ------------------------------------------------- context and publishing

def test_the_preview_pane_is_the_readers_renderer(env):
    client, _db, _idir = env
    html = _room(client)
    assert f'src="{BASE}/edit/full-preview"' in html
    assert "<iframe" in html
    # not production: production has no unpublished issue in it
    assert "vercel.app" not in html.split("<iframe")[1].split(">")[0]


def test_research_and_qa_ride_along_with_the_section(env):
    client, _db, _idir = env
    html = _room(client)
    assert set(re.findall(ATTR.format("research"), html)) == set(
        re.findall(ATTR.format("pane"), html))
    assert 'data-ctx="qa"' in html and "PUBLICATION CHECK" in html.upper()


def test_publish_is_reachable_without_leaving_the_room(env):
    client, db, _idir = env
    _make_ready(client, db)
    html = _room(client)
    assert "READY" in html
    assert 'id="pubdlg"' in html and "showModal()" in html
    assert f'action="{BASE}/edit/publish-start"' in html
    # every safeguard the full screen has
    assert 'name="confirm_deploy"' in html and "required" in html
    assert "confirm(" in html
    assert f'href="{BASE}/edit/publish"' in html, "the full screen is still one click away"


def test_the_room_and_the_publish_screen_read_one_publication_state(env):
    """Both call _add_publication_state; two readings that could disagree
    is the bug this project keeps having to fix."""
    src = pathlib.Path("leaguepage/desk_editor.py").read_text(encoding="utf-8")
    assert src.count("_add_publication_state(") == 3   # the def plus two callers
    client, db, _idir = env
    _make_ready(client, db)
    room = _room(client)
    screen = client.get(f"{BASE}/edit/publish").text
    assert "never published" in room and "never deployed" in screen


def test_blockers_refuse_publication_in_the_drawer_too(env):
    client, _db, _idir = env
    html = _room(client)          # nothing approved yet
    assert "BLOCKED" in html
    assert "Publishing is refused until the blockers clear." in html
    assert 'name="confirm_deploy"' not in html.split('id="pubdlg"')[1]


# ------------------------------------------------- safety and shape

def test_the_room_is_private_and_carries_no_new_public_surface():
    src = pathlib.Path("leaguepage/desk.py").read_text(encoding="utf-8")
    public = src[src.index("PUBLIC_PATHS = {"):]
    public = public[:public.index("}") + 1]
    assert "room" not in public and "desk-editor.js" not in public


def test_the_old_editor_still_works_during_migration(env):
    """Nothing is retired until the room has run a real week."""
    client, _db, _idir = env
    r = client.get(f"{BASE}/edit")
    assert r.status_code == 200 and "Original words." in r.text


def test_the_room_survives_an_issue_with_nothing_written_yet(env, tmp_path):
    client, _db, idir = env
    (idir / "lowdown" / "lowdown.md").unlink()
    html = _room(client)
    assert "needs writing" in html and "BLOCKED" in html
