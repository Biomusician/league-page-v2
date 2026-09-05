"""What the weekly editor puts in front of him, and in what order.

The Desk had come to present everything as equally important: eleven
sections sprung open because eleven were unapproved, the team-name panel
sat above the writing, and the masthead -- which he never writes -- was in
the list of things to update.

So the page is ordered by what he actually does every week. The Lowdown and
the matchups first, then the week's special sections, then the standing
ones, then Weekly Hardware, which closes the paper. Everything that is not
this week's writing is below a line, and nothing opens itself.

These assert the contract against the rendered page rather than against a
screenshot, so it stays true.
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
from leaguepage.storage import Storage

from fixtures import populate_league, populate_matchups

SEASON = "2026"
LG = get_league("surfeit")
EDIT = f"/commissioner/surfeit/{SEASON}/issue/week-01/edit"


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
    return TestClient(create_app(db_path=db)), db


def _page(client):
    r = client.get(EDIT)
    assert r.status_code == 200, r.text
    return r.text


def _module_ids(html):
    """Weekly module cards, in document order."""
    body = html[html.index("</style>"):]
    return [m.group(1) for m in
            re.finditer(r'<details class="sec card" id="sec-([a-z0-9:-]+)"', body)]


# ------------------------------------------------------------ order

def test_the_week_runs_lowdown_matchups_customs_then_hardware(env):
    client, _db = env
    ids = _module_ids(_page(client))
    assert ids[0] == "lowdown"
    assert ids[1] == "ctp"
    assert ids[-1] == "hardware", ids


def test_hardware_closes_the_issue_whatever_else_is_there(env):
    """Adding sections, excluding sections and renaming them must not move
    the closer."""
    client, db = env
    for _ in range(3):
        client.post(f"{EDIT}/custom", data={"action": "add"}, follow_redirects=True)
    client.post(f"{EDIT}/module", data={"module_key": "tracks", "action": "exclude"},
                follow_redirects=True)
    ids = _module_ids(_page(client))
    assert ids[-1] == "hardware"
    assert sum(1 for i in ids if i.startswith("custom")) == 3


def test_custom_sections_sit_after_the_matchups_and_before_hardware(env):
    client, _db = env
    client.post(f"{EDIT}/custom", data={"action": "add"}, follow_redirects=True)
    ids = _module_ids(_page(client))
    c = ids.index("custom")
    assert ids.index("ctp") < c < ids.index("hardware")


# ------------------------------------------------------------ collapse

def test_nothing_is_open_when_the_page_loads(env):
    """Not the sections, not the matchups inside them, not the team-name
    panel. A page that opens eleven cards is one he closes before he reads."""
    client, _db = env
    html = _page(client)
    body = html[html.index("</style>"):]
    assert "<details" in body
    for m in re.finditer(r"<details[^>]*>", body):
        assert " open" not in m.group(0), m.group(0)[:90]


def test_a_warning_does_not_open_a_section(env):
    """`has a problem` is not a reason to seize the screen."""
    client, _db = env
    html = _page(client)
    assert "blockers" in html                      # there are warnings
    assert re.search(r'id="sec-lowdown"[^>]*open', html) is None


def test_expand_all_still_exists(env):
    client, _db = env
    html = _page(client)
    assert "expandAll(true)" in html and "collapseApproved" in html


# ------------------------------------------------------------ hierarchy

def test_the_masthead_is_not_one_of_the_weeks_jobs(env):
    """It publishes; he does not write it."""
    client, _db = env
    html = _page(client)
    assert "sec-masthead" in html                  # still reachable
    weekly = html[:html.index('<h2 class="adminhead">')]
    assert "sec-masthead" not in weekly


def test_administration_sits_below_the_writing(env):
    client, _db = env
    html = _page(client)
    head = html.index('<h2 class="adminhead">')
    assert html.index('id="sec-lowdown"') < head
    assert html.index('id="sec-hardware"') < head
    # the CARD, not any mention: the blockers panel above links to
    # `sec-team-names` as its fallback anchor
    for admin in ('id="sec-team-names"', 'id="sec-masthead"'):
        assert html.index(admin) > head, admin


def test_the_about_editor_is_a_quiet_link_not_a_section(env):
    client, _db = env
    html = _page(client)
    assert '/commissioner/site/about' in html
    assert 'class="meta sitelink"' in html
    assert 'id="sec-about"' not in html


# ------------------------------------------------------------ custom UX

def test_add_custom_creates_one_included_unapproved_section(env):
    client, db = env
    client.post(f"{EDIT}/custom", data={"action": "add"}, follow_redirects=True)
    with Storage(db) as s:
        rows = s.get_issue_modules("surfeit", SEASON, "week-01")
    assert "custom" in rows
    assert rows["custom"]["included"] == 1 and rows["custom"]["approved"] == 0


def test_a_new_issue_has_no_custom_section_until_one_is_made(env):
    client, db = env
    with Storage(db) as s:
        assert not [k for k in s.get_issue_modules("surfeit", SEASON, "week-01")
                    if k.startswith("custom")]
    assert "sec-custom" not in _page(client)


def test_custom_sections_can_be_renamed(env):
    client, _db = env
    client.post(f"{EDIT}/custom", data={"action": "add"}, follow_redirects=True)
    client.post(f"{EDIT}/custom",
                data={"action": "rename", "module_key": "custom",
                      "title": "Shame! Shame! Shame!"}, follow_redirects=True)
    assert "Shame! Shame! Shame!" in _page(client)


def test_renaming_a_section_that_does_not_exist_is_refused(env):
    client, _db = env
    r = client.post(f"{EDIT}/custom",
                    data={"action": "rename", "module_key": "custom-9",
                          "title": "Ghost"})
    assert r.status_code == 404


def test_there_is_no_delete_button_for_a_custom_section(env):
    """Excluding keeps the prose. A control that destroys writing is not
    worth the two clicks it saves."""
    client, _db = env
    client.post(f"{EDIT}/custom", data={"action": "add"}, follow_redirects=True)
    html = _page(client)
    assert 'value="delete"' not in html
    assert "Exclude from issue" in html


# ------------------------------------------------------------ responsive

def _css():
    return (pathlib.Path("templates/desk/base.html").read_text(encoding="utf-8")
            + pathlib.Path("templates/desk/editor.html").read_text(encoding="utf-8")
            + pathlib.Path("templates/desk/_takes_panel.html").read_text(encoding="utf-8")
            + pathlib.Path("templates/desk/_qa_panel.html").read_text(encoding="utf-8"))


def test_no_control_is_sized_below_a_finger():
    """44px. Three panels had their own smaller min-heights, which beat the
    base rule on specificity and left 42px targets on a phone."""
    css = _css()
    for m in re.finditer(r"min-height:\s*([\d.]+)rem", css):
        val = float(m.group(1))
        # 0 is the deliberate wide-screen reset; anything else that sizes a
        # control has to clear 44px.
        assert val == 0 or val >= 2.75, f"{m.group(0)} is under 44px"


def test_the_writing_box_does_not_zoom_on_ios():
    """Below 16px, Safari zooms on focus and never zooms back, on the one
    surface he actually writes in."""
    css = pathlib.Path("templates/desk/editor.html").read_text(encoding="utf-8")
    m = re.search(r"textarea\.prose\s*\{[^}]*font-size:\s*([\d.]+)rem", css)
    assert m and float(m.group(1)) >= 1.0


def test_a_nested_matchup_does_not_eat_a_phone_screen():
    css = pathlib.Path("templates/desk/editor.html").read_text(encoding="utf-8")
    assert "details.sec.child" in css
    narrow = css[css.index("@media (max-width:30rem)"):]
    assert "margin-left:0" in narrow.replace(" ", "")
