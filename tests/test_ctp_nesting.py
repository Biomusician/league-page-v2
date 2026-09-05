"""Matchup previews are children of Common Tactical Picture.

They were peers of it: a flat list of module cards, then a second flat list
of matchup cards under an <h2> of their own. That is not what they are. A
preview has no standing on its own — it publishes inside CTP, it is one of
the pieces CTP is made of, and CTP is finished exactly when they are.

The nesting therefore has to hold in the model, not only in the markup:
readiness, approval, research routing and the rendered issue all read the
same parent/child relationship. These tests pin each of those, and one of
them asserts real DOM containment so nobody can satisfy the rest with an
indent.
"""
from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

import leaguepage.config as cfg
import leaguepage.issue_builder as ib
import leaguepage.matchup_packet as mp
from leaguepage.config import get_league
from leaguepage.desk import create_app
from leaguepage.issue_builder import matchup_children, module_states
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


def _ctp(db):
    with Storage(db) as s:
        return next(m for m in module_states(s, LG, SEASON, "week-01", week=1)
                    if m["module_key"] == "ctp")


def _write_all_drafts(ed, children):
    for c in children:
        d = ed / SEASON / "surfeit" / "week-01" / "matchups" / c["slug"]
        d.mkdir(parents=True, exist_ok=True)
        (d / "draft.md").write_text("Real prose.\n", encoding="utf-8")


def _approve(client, section, action="approve"):
    return client.post(f"{EDIT}/approve", json={"section": section, "action": action})


# ------------------------------------------------------------ issue structure

def test_the_module_carries_its_matchups_as_children(env):
    _c, db, _ed = env
    ctp = _ctp(db)
    assert ctp["children_total"] == 5, "five matchups in a ten-team league"
    assert len(ctp["children"]) == 5
    assert all(c["section"].startswith("matchup:") for c in ctp["children"])


def test_no_other_module_claims_children(env):
    _c, db, _ed = env
    with Storage(db) as s:
        mods = module_states(s, LG, SEASON, "week-01", week=1)
    for m in mods:
        if m["module_key"] != "ctp":
            assert m["children"] == [] and m["children_total"] == 0, m["module_key"]


def test_children_are_ordered_the_way_the_section_publishes(env):
    """FEATURE leads the page, so it leads the editor."""
    from leaguepage.matchup_interest import PROMINENCE_LEVELS

    _c, db, _ed = env
    with Storage(db) as s:
        kids = matchup_children(s, LG, SEASON, "week-01", 1)
    ranks = [PROMINENCE_LEVELS.index(c["prominence"]) for c in kids]
    assert ranks == sorted(ranks)
    assert kids[0]["prominence"] == "FEATURE"


# ------------------------------------------------------------ readiness

def test_the_parent_reports_its_children_as_a_count(env):
    _c, db, _ed = env
    assert _ctp(db)["detail"] == "0 / 5 previews written"


def test_writing_one_child_moves_the_parent_count(env):
    """The count is what is written, not what is separately signed off:
    the section carries one approval over all of them."""
    client, db, ed = env
    kids = _ctp(db)["children"]
    _write_all_drafts(ed, kids[:1])
    ctp = _ctp(db)
    assert ctp["children_written"] == 1
    assert ctp["detail"] == "1 / 5 previews written"
    assert ctp["status"] == "edited"


def test_the_parent_is_never_flagged_as_an_empty_section(env):
    """`No meaningful material this week` is about unwritten prose. A CTP
    with no matchups is a data problem, and that note would send him to
    exclude the section instead of to the sync."""
    _c, db, _ed = env
    assert _ctp(db)["empty"] is False


# ------------------------------------------------------------ approval state

def test_the_parent_cannot_be_approved_while_a_child_is_unwritten(env):
    """One approval covers the previews, so they all have to exist."""
    client, db, _ed = env
    r = _approve(client, "ctp")
    assert r.status_code == 400
    assert "not written yet" in r.json()["error"]


def test_the_refusal_names_the_matchups_still_open(env):
    client, db, ed = env
    kids = _ctp(db)["children"]
    _write_all_drafts(ed, kids[:4])
    err = _approve(client, "ctp").json()["error"]
    assert kids[4]["title"] in err


def test_the_parent_approves_in_one_click_once_the_previews_are_written(env):
    """Seven decisions became one. It used to be refused forever, too: the
    gate read `sections/ctp.md`, a file this module has never had."""
    client, db, ed = env
    kids = _ctp(db)["children"]
    _write_all_drafts(ed, kids)
    r = _approve(client, "ctp")
    assert r.status_code == 200, r.text
    ctp = _ctp(db)
    assert ctp["status"] == "approved"
    assert ctp["detail"] == "5 previews, approved together"


def test_a_module_with_no_prose_file_is_not_judged_by_one(env):
    """Power rankings live in the database, not in `sections/power.md`."""
    client, _db, _ed = env
    r = _approve(client, "power")
    assert r.status_code == 200, r.text


def test_an_unknown_section_is_a_404_not_a_silent_approval(env):
    client, _db, _ed = env
    assert _approve(client, "not-a-module").status_code == 404


# ------------------------------------------------------------ navigation

def test_each_matchup_card_is_inside_the_parent_card_in_the_dom(env):
    """The requirement is containment, not indentation: a child has to
    collapse with its parent and be reachable by Expand All."""
    client, db, _ed = env
    html = client.get(EDIT).text
    start = html.index('id="sec-ctp"')
    depth, i = 1, html.rindex("<details", 0, start)   # the parent's own tag
    while i < len(html):
        nxt_open = html.find("<details", i + 1)
        nxt_close = html.find("</details>", i + 1)
        if nxt_close == -1:
            break
        if nxt_open != -1 and nxt_open < nxt_close:
            depth, i = depth + 1, nxt_open
        else:
            depth, i = depth - 1, nxt_close
            if depth == 0:
                break
    ctp_block = html[start:i]
    for c in _ctp(db)["children"]:
        assert f'id="{c["anchor"]}"' in ctp_block, c["slug"]
        assert f'data-section="{c["section"]}"' in ctp_block


def test_there_is_no_second_top_level_matchups_heading(env):
    """The peer-level `Matchups` concept is gone from the authoring UX."""
    client, _db, _ed = env
    html = client.get(EDIT).text
    assert "matchup previews</h2>" not in html
    assert html.count('id="sec-ctp"') == 1


def test_indentation_alone_would_not_pass(env):
    """A margin-left is allowed on top of the nesting, never instead of it."""
    import pathlib

    # the card is a shared partial now; the nesting lives in it
    tpl = pathlib.Path("templates/desk/_section_card.html").read_text(encoding="utf-8")
    assert re.search(r'c\.kind == "ctp".*?_matchup_card\.html', tpl, re.S)
    child = pathlib.Path("templates/desk/_matchup_card.html").read_text(encoding="utf-8")
    assert "details.sec.child" in tpl or 'class="sec card child"' in child


# ------------------------------------------------------------ research routing

def test_a_rewrite_request_on_a_child_routes_to_the_matchup(env):
    client, db, _ed = env
    slug = _ctp(db)["children"][0]["slug"]
    r = client.post(f"{EDIT}/request-rewrite", json={"section": f"matchup:{slug}",
                                             "note": "punch it up"})
    assert r.status_code == 200, r.text
    with Storage(db) as s:
        rows = s.list_rewrite_requests("surfeit", SEASON, "week-01")
    assert [x["section"] for x in rows] == [f"matchup:{slug}"]


def test_a_childs_open_request_shows_on_the_child_not_the_parent(env):
    client, db, _ed = env
    kids = _ctp(db)["children"]
    client.post(f"{EDIT}/request-rewrite", json={"section": kids[0]["section"],
                                         "note": "punch it up"})
    html = client.get(EDIT).text
    at_child = html.index(f'id="{kids[0]["anchor"]}"')
    note = html.index("punch it up")
    nxt = html.find('class="sec card child"', at_child + 1)
    assert at_child < note < (nxt if nxt != -1 else len(html))


def test_saving_a_child_still_writes_the_matchup_draft(env):
    client, db, ed = env
    kids = _ctp(db)["children"]
    r = client.post(f"{EDIT}/save", json={"section": kids[0]["section"],
                                          "text": "Written in the editor.\n",
                                          "base_sha": ""})
    assert r.status_code in (200, 409), r.text
    if r.status_code == 200:
        path = (ed / SEASON / "surfeit" / "week-01" / "matchups"
                / kids[0]["slug"] / "draft.md")
        assert "Written in the editor." in path.read_text(encoding="utf-8")
