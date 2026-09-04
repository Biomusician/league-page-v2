"""Two dozen fields retyped a week, and twenty-six clicks to approve an issue.

Neither is a bug. Both are the difference between a Sunday evening that takes
forty minutes and one that takes two hours, which is the difference between a
newsletter that ships every week and one that does not.

Mutation testing runs against a fixture database. Nothing here touches the
real one, approves a real section, or writes a real ranking.
"""
from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from leaguepage.config import get_league
from leaguepage.desk import _previous_ranking_label, create_app
from leaguepage.storage import Storage

from season import populate_season

SEASON = "2026"
DISCO = get_league("disco")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("LEAGUEPAGE_AUTH_MODE", "off")
    db = tmp_path / "d.sqlite3"
    with Storage(db) as s:
        populate_season(s, DISCO, teams=12, weeks_played=4, current_week=4,
                        season=SEASON, seed=7)
        populate_season(s, get_league("surfeit"), teams=10, weeks_played=4,
                        current_week=4, season=SEASON, seed=8)
    return TestClient(create_app(db), follow_redirects=False), db


# --------------------------------------------------- which board is "prev"

@pytest.mark.parametrize("label,expected", [
    ("preseason", None),
    ("week-01", "preseason"),
    ("week-05", "week-04"),
    ("week-14", "week-13"),
    ("draft", None),
])
def test_a_week_is_compared_against_the_week_before_it(label, expected):
    """"Prev" was always the PRESEASON board, so week 5 compared itself
    against August and the movement arrows measured the wrong gap."""
    assert _previous_ranking_label(label) == expected


# ------------------------------------------------------ seeding the board

def _rankings(client, label):
    return client.get(
        f"/commissioner/disco/{SEASON}/rankings/{label}").text


def _values(html, prefix):
    return re.findall(rf'name="{prefix}_\d+"[^>]*value="([^"]*)"', html)


def test_an_empty_board_is_seeded_from_the_previous_one(client):
    """Twenty-four fields retyped a week, with last week's board one page
    away."""
    c, db = client
    with Storage(db) as s:
        s.save_power_rankings("disco", SEASON, "week-03", [
            {"roster_id": rid, "rank": rid, "tier": 1 + rid // 4,
             "note": f"last week's words about {rid}"}
            for rid in range(1, 13)])
    page = _rankings(c, "week-04")
    assert "Nothing saved for week-04 yet" in page
    assert [v for v in _values(page, "rank") if v] == [str(i) for i in range(1, 13)]
    assert [v for v in _values(page, "tier") if v]


def test_the_commissioner_s_own_words_are_never_carried_forward(client):
    """A note is prose he wrote about a specific week. Copying it into the
    next issue under his name is not a convenience."""
    c, db = client
    with Storage(db) as s:
        s.save_power_rankings("disco", SEASON, "week-03", [
            {"roster_id": rid, "rank": rid, "tier": 1,
             "note": f"last week's words about {rid}"}
            for rid in range(1, 13)])
    page = _rankings(c, "week-04")
    assert "last week's words" not in page
    assert _values(page, "note") == [""] * 12


def test_a_board_that_already_has_rows_is_not_overwritten(client):
    """Seeding is for an empty board. Anything else would silently replace
    work already done."""
    c, db = client
    with Storage(db) as s:
        s.save_power_rankings("disco", SEASON, "week-03",
                              [{"roster_id": rid, "rank": rid, "tier": 1,
                                "note": ""} for rid in range(1, 13)])
        s.save_power_rankings("disco", SEASON, "week-04",
                              [{"roster_id": 1, "rank": 12, "tier": 4,
                                "note": "mine"}])
    page = _rankings(c, "week-04")
    assert "Nothing saved for week-04 yet" not in page
    assert "12" in _values(page, "rank")
    assert "mine" in page


def test_nothing_is_saved_by_looking_at_the_screen(client):
    """The seeded values are a starting point in a form, not a write."""
    c, db = client
    with Storage(db) as s:
        s.save_power_rankings("disco", SEASON, "week-03",
                              [{"roster_id": rid, "rank": rid, "tier": 1,
                                "note": ""} for rid in range(1, 13)])
    _rankings(c, "week-04")
    with Storage(db) as s:
        assert s.get_power_rankings("disco", SEASON, "week-04") == []


def test_the_preseason_board_has_nothing_to_seed_from(client):
    c, _ = client
    assert "Nothing saved for preseason yet" not in _rankings(c, "preseason")


# ------------------------------------------------------------ bulk approve

def test_bulk_approve_has_no_endpoint_of_its_own(client):
    """It calls the same per-section approve, once each, so every section
    passes exactly the gate it would have passed alone. A bulk endpoint would
    be a second path to approval, and approval must have only one."""
    import pathlib

    js = pathlib.Path("templates/desk/editor.html").read_text(encoding="utf-8")
    body = js[js.index("async function approveAllReady"):
              js.index("function collapseApproved")]
    assert 'EDIT + "/approve"' in body
    assert body.count("fetch(") == 1
    for banned in ("/approve-all", "/bulk", "force", "skip"):
        assert banned not in body, banned


def test_bulk_approve_reports_what_it_refused(client):
    """Silently skipping an empty section is worse than not offering the
    button: he would believe the issue was approved."""
    import pathlib

    js = pathlib.Path("templates/desk/editor.html").read_text(encoding="utf-8")
    body = js[js.index("async function approveAllReady"):
              js.index("function collapseApproved")]
    assert "refused" in body and "Left alone" in body
    assert "confirm(" in body


def test_bulk_approve_asks_first(client):
    import pathlib

    js = pathlib.Path("templates/desk/editor.html").read_text(encoding="utf-8")
    assert "approveAllReady()" in js
    body = js[js.index("async function approveAllReady"):
              js.index("function collapseApproved")]
    assert body.index("confirm(") < body.index('fetch(')


def test_the_per_section_gate_still_refuses_an_empty_section(client, tmp_path):
    """Whatever the bulk button does, this is the check it goes through."""
    c, _ = client
    r = c.post(f"/commissioner/disco/{SEASON}/issue/week-04/edit/approve",
               json={"section": "lowdown", "action": "approve"})
    assert r.status_code == 400
    assert "empty" in r.json()["error"]
