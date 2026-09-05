"""Defects two adversarial passes found, each pinned so it cannot return.

None of these were covered by the suite when they were introduced, which is
the point of writing them down: a green suite is evidence about what it
tests, and every one of these shipped past a green suite.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import leaguepage.config as cfg
import leaguepage.issue_builder as ib
import leaguepage.matchup_packet as mp
from leaguepage.config import get_league
from leaguepage.desk import create_app
from leaguepage.issue_builder import assemble_issue, module_states
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
    return TestClient(create_app(db_path=db)), db, ed


def _exclude_everything(client, db):
    with Storage(db) as s:
        keys = [m["module_key"] for m in
                module_states(s, LG, SEASON, "week-01", week=1)]
    for k in keys:
        client.post(f"{EDIT}/module", data={"module_key": k, "action": "exclude"},
                    follow_redirects=True)


# ------------------------------------------------------- the empty issue

def test_an_empty_issue_never_reports_ready(env):
    """It produced no warnings, so every gate passed and the Desk said
    READY TO PUBLISH. Publishing froze a blank page into the immutable
    record, and the republish guard then refused to correct it in place.
    Excluding sections one at a time to see what a thin week looks like is
    an ordinary thing to do."""
    client, db, _ed = env
    _exclude_everything(client, db)
    with Storage(db) as s:
        assembled = assemble_issue(s, LG, SEASON, "week-01", week=1)
    assert assembled["warnings"], "an issue with nothing in it must warn"
    assert any(r["kind"] == "empty-issue" for r in assembled["warning_rows"])


def test_an_empty_issue_cannot_be_published(env):
    client, db, _ed = env
    _exclude_everything(client, db)
    with Storage(db) as s:
        with pytest.raises(ValueError, match="no publishable content"):
            assemble_issue(s, LG, SEASON, "week-01", week=1, enforce=True)


def test_the_editor_shows_the_empty_issue_as_a_blocker(env):
    client, db, _ed = env
    _exclude_everything(client, db)
    page = client.get(EDIT).text
    assert "READY TO PUBLISH" not in page
    assert "no publishable content" in page


# --------------------------------------------- blockers know their section

def test_two_sections_with_one_title_get_their_own_blockers(env):
    """The Desk matched a warning back to its section by TITLE. Two custom
    sections called the same thing collided: the second was unreachable
    from the blocker list, because its anchor and its Exclude button both
    pointed at the first."""
    client, db, _ed = env
    for _ in range(2):
        client.post(f"{EDIT}/custom", data={"action": "add"}, follow_redirects=True)
    for key in ("custom", "custom-2"):
        client.post(f"{EDIT}/custom",
                    data={"action": "rename", "module_key": key, "title": "Twin"},
                    follow_redirects=True)
    with Storage(db) as s:
        rows = assemble_issue(s, LG, SEASON, "week-01", week=1)["warning_rows"]
    keys = [r["module_key"] for r in rows if r["kind"] == "empty-section"]
    assert "custom" in keys and "custom-2" in keys


def test_a_custom_named_after_a_standing_section_does_not_steal_its_button(env):
    """A custom section titled `Fades` handed him an Exclude button that
    removed the standing Fades module instead."""
    client, db, _ed = env
    client.post(f"{EDIT}/custom", data={"action": "add"}, follow_redirects=True)
    client.post(f"{EDIT}/custom",
                data={"action": "rename", "module_key": "custom", "title": "Fades"},
                follow_redirects=True)
    with Storage(db) as s:
        rows = assemble_issue(s, LG, SEASON, "week-01", week=1)["warning_rows"]
    by_key = {r["module_key"] for r in rows if r["kind"] == "empty-section"}
    assert "custom" in by_key and "fades" in by_key


# ------------------------------------------- retired modules stay retired

def test_unapproving_an_unknown_section_is_refused(env):
    """The write was unconditional and omitted `included`, which defaults
    to 1, so unapproving a key created it -- included."""
    client, db, _ed = env
    r = client.post(f"{EDIT}/approve",
                    json={"section": "zzz-unknown", "action": "unapprove"})
    assert r.status_code == 404
    with Storage(db) as s:
        assert "zzz-unknown" not in s.get_issue_modules("surfeit", SEASON, "week-01")


def test_unapproving_a_retired_module_does_not_resurrect_it(env):
    client, db, _ed = env
    r = client.post(f"{EDIT}/approve",
                    json={"section": "forceflow", "action": "unapprove"})
    assert r.status_code == 404
    with Storage(db) as s:
        rows = s.get_issue_modules("surfeit", SEASON, "week-01")
        keys = {m["module_key"] for m in
                module_states(s, LG, SEASON, "week-01", week=1)}
    assert "forceflow" not in rows and "forceflow" not in keys


def test_a_real_section_still_unapproves(env):
    """The gate must not have broken the ordinary case."""
    client, db, _ed = env
    r = client.post(f"{EDIT}/approve", json={"section": "tracks", "action": "unapprove"})
    assert r.status_code == 200, r.text


# ------------------------------------------------------------- positions

def test_a_position_orders_custom_sections(env):
    """The comment claimed positions ordered customs and the sort never
    reached them, so `editor_custom` was writing dead data."""
    client, db, _ed = env
    for _ in range(3):
        client.post(f"{EDIT}/custom", data={"action": "add"}, follow_redirects=True)
    with Storage(db) as s:
        for key, pos in (("custom", 30), ("custom-2", 20), ("custom-3", 10)):
            s.set_issue_module(league_slug="surfeit", season=SEASON,
                               issue_key="week-01", module_key=key, position=pos)
        keys = [m["module_key"] for m in
                module_states(s, LG, SEASON, "week-01", week=1)
                if m["module_key"].startswith("custom")]
    assert keys == ["custom-3", "custom-2", "custom"]


def test_a_position_still_cannot_move_hardware(env):
    client, db, _ed = env
    with Storage(db) as s:
        for key, pos in (("hardware", -5), ("lowdown", 999999)):
            s.set_issue_module(league_slug="surfeit", season=SEASON,
                               issue_key="week-01", module_key=key, position=pos)
        keys = [m["module_key"] for m in
                module_states(s, LG, SEASON, "week-01", week=1)]
    assert keys[-1] == "hardware"


def test_a_non_numeric_position_does_not_crash_the_sort(env):
    """`position` tolerates text, and comparing a string against an int
    raises rather than sorting."""
    client, db, _ed = env
    for _ in range(2):
        client.post(f"{EDIT}/custom", data={"action": "add"}, follow_redirects=True)
    with Storage(db) as s:
        s._conn.execute(  # noqa: SLF001 - deliberately hostile value
            "UPDATE issue_modules SET position='oops' WHERE module_key='custom'")
        s._conn.commit()
        keys = [m["module_key"] for m in
                module_states(s, LG, SEASON, "week-01", week=1)]
    assert keys[-1] == "hardware"


def test_a_malformed_move_does_not_return_500(env):
    """`.lstrip('-')` accepted '--5', which raised out of int()."""
    client, _db, _ed = env
    for bad in ("--5", "-", "3.5", "", "  ", "1e5"):
        r = client.post(f"{EDIT}/module",
                        data={"module_key": "tracks", "action": "move",
                              "position": bad}, follow_redirects=False)
        assert r.status_code < 500, (bad, r.status_code)


# ------------------------------------------------- custom numbering

def test_a_reused_custom_key_is_numbered_from_the_key(env):
    """Counting existing rows handed a section reusing a freed key the
    wrong number and a duplicate position."""
    client, db, _ed = env
    for _ in range(3):
        client.post(f"{EDIT}/custom", data={"action": "add"}, follow_redirects=True)
    with Storage(db) as s:
        s._conn.execute(  # noqa: SLF001 - simulate a freed key
            "DELETE FROM issue_modules WHERE module_key='custom-2'")
        s._conn.commit()
    client.post(f"{EDIT}/custom", data={"action": "add"}, follow_redirects=True)
    with Storage(db) as s:
        rows = s.get_issue_modules("surfeit", SEASON, "week-01")
    assert rows["custom-2"]["position"] == 2
    positions = [rows[k]["position"] for k in ("custom", "custom-2", "custom-3")]
    assert len(set(positions)) == 3, positions
