"""Common Tactical Picture is approved once, over what it publishes.

The old model asked for seven decisions: approve each of six previews,
then approve the section that is exactly those six previews. Worse, a
preview that was not individually approved vanished from the page without
saying so, because approval decided membership.

Now the published unit carries the approval, and the approval carries a
signature over the exact text it covered. Editing any preview, or the
opening remarks, makes it stale on its own — nothing has to notice.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import leaguepage.config as cfg
import leaguepage.issue_builder as ib
import leaguepage.matchup_packet as mp
from leaguepage.config import get_league
from leaguepage.desk import create_app
from leaguepage.issue_builder import (assemble_issue, ctp_approved, ctp_signature,
                                      matchup_children, module_states)
from leaguepage.storage import Storage

from fixtures import populate_league, populate_matchups

SEASON = "2027"
LG = get_league("surfeit")
BASE = f"/commissioner/surfeit/{SEASON}/issue/week-01"
EDIT = f"{BASE}/edit"


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
    (idir / "sections").mkdir(parents=True)
    client = TestClient(create_app(db_path=db))
    with Storage(db) as s:
        kids = matchup_children(s, LG, SEASON, "week-01", 1)
    for i, k in enumerate(kids):
        d = idir / "matchups" / k["slug"]
        d.mkdir(parents=True, exist_ok=True)
        (d / "draft.md").write_text(f"Preview number {i}, written by the Commissioner.\n",
                                    encoding="utf-8")
    return client, db, idir, kids


def _ctp(db):
    with Storage(db) as s:
        mods = module_states(s, LG, SEASON, "week-01", week=1)
    return next(m for m in mods if m["module_key"] == "ctp")


def _approved(db):
    with Storage(db) as s:
        return ctp_approved(s, LG, SEASON, "week-01", 1)


def _approve_ctp(client):
    r = client.post(f"{EDIT}/approve", json={"section": "ctp", "action": "approve"})
    assert r.status_code == 200, r.text
    return r


def _body(db):
    with Storage(db) as s:
        return next((x["content_md"] for x in
                     assemble_issue(s, LG, SEASON, "week-01", week=1)["sections"]
                     if x["module_key"] == "ctp"), None)


# --------------------------------------------- one approval, not seven

def test_a_written_preview_publishes_without_its_own_approval(env):
    """Membership used to depend on individual sign-off, so an unapproved
    preview disappeared from the page silently."""
    client, db, _idir, kids = env
    body = _body(db)
    assert body is not None
    for i in range(len(kids)):
        assert f"Preview number {i}," in body
    assert _ctp(db)["detail"] == f"{len(kids)} previews written; approve the section"


def test_one_click_approves_the_whole_section(env):
    client, db, _idir, kids = env
    assert not _approved(db)
    _approve_ctp(client)
    assert _approved(db)
    assert _ctp(db)["status"] == "approved"
    assert _ctp(db)["detail"] == f"{len(kids)} previews, approved together"


def test_the_approval_records_what_it_covered(env):
    client, db, _idir, _kids = env
    _approve_ctp(client)
    with Storage(db) as s:
        row = (s.get_issue_modules("surfeit", SEASON, "week-01") or {})["ctp"]
        assert row["approved_sha"] == ctp_signature(s, LG, SEASON, "week-01", 1)
        assert row["approved_sha"]


# --------------------------------------------- it goes stale on its own

def test_editing_any_preview_makes_the_approval_stale(env):
    client, db, idir, kids = env
    _approve_ctp(client)
    assert _approved(db)
    r = client.post(f"{EDIT}/save",
                    json={"section": kids[2]["section"], "base_sha": "",
                          "text": "Preview number 2, rewritten on Saturday.\n"})
    assert r.status_code == 200, r.text
    assert not _approved(db), "the section no longer matches what was signed off"
    assert _ctp(db)["status"] == "edited"
    assert "approve the section" in _ctp(db)["detail"]


def test_editing_the_opening_remarks_makes_it_stale_too(env):
    client, db, _idir, _kids = env
    _approve_ctp(client)
    client.post(f"{EDIT}/save",
                json={"section": "ctp", "text": "One game matters.\n", "base_sha": ""})
    assert not _approved(db)
    _approve_ctp(client)
    assert _approved(db)


def test_restoring_the_exact_text_makes_the_approval_true_again(env):
    """The signature is over the text, so putting the text back is enough;
    nothing has to remember that it was once approved."""
    client, db, idir, kids = env
    original = (idir / "matchups" / kids[0]["slug"] / "draft.md").read_text(encoding="utf-8")
    _approve_ctp(client)
    client.post(f"{EDIT}/save", json={"section": kids[0]["section"],
                                      "text": "Something else entirely.\n", "base_sha": ""})
    assert not _approved(db)
    client.post(f"{EDIT}/save", json={"section": kids[0]["section"],
                                      "text": original, "base_sha": ""})
    assert _approved(db)


def test_a_new_preview_appearing_makes_the_approval_stale(env):
    """Approving six previews is not approving a seventh."""
    client, db, idir, kids = env
    _approve_ctp(client)
    with Storage(db) as s:
        before = ctp_signature(s, LG, SEASON, "week-01", 1)
    (idir / "matchups" / kids[1]["slug"] / "draft.md").write_text(
        "Preview number 1, and a whole new paragraph.\n", encoding="utf-8")
    with Storage(db) as s:
        assert ctp_signature(s, LG, SEASON, "week-01", 1) != before
    assert not _approved(db)


# --------------------------------------------- the publication gate

def test_publication_is_refused_while_the_approval_is_stale(env):
    client, db, idir, kids = env
    _approve_ctp(client)
    with Storage(db) as s:
        assembled = assemble_issue(s, LG, SEASON, "week-01", week=1)
    assert not [w for w in assembled["warning_rows"]
                if w["module_key"] == "ctp" and w["kind"] == "unapproved"]
    client.post(f"{EDIT}/save", json={"section": kids[3]["section"],
                                      "text": "Rewritten after sign-off.\n", "base_sha": ""})
    with Storage(db) as s:
        assembled = assemble_issue(s, LG, SEASON, "week-01", week=1)
    assert [w for w in assembled["warning_rows"]
            if w["module_key"] == "ctp" and w["kind"] == "unapproved"], \
        "a stale approval must not publish newer text"


def test_unapproving_clears_the_signature(env):
    client, db, _idir, _kids = env
    _approve_ctp(client)
    client.post(f"{EDIT}/approve", json={"section": "ctp", "action": "unapprove"})
    with Storage(db) as s:
        row = (s.get_issue_modules("surfeit", SEASON, "week-01") or {})["ctp"]
    assert not row["approved"] and not row["approved_sha"]


# --------------------------------------------- migration and the Desk

def test_an_approval_from_before_signatures_is_grandfathered(env):
    """Issues approved and published before this existed keep their
    sign-off. Un-approving shipped work to admit we cannot prove what it
    said would be the worse lie."""
    client, db, _idir, _kids = env
    with Storage(db) as s:
        s.set_issue_module(league_slug="surfeit", season=SEASON, issue_key="week-01",
                           module_key="ctp", approved=1, approved_sha=None)
        assert ctp_approved(s, LG, SEASON, "week-01", 1)


def test_the_preview_cards_no_longer_ask_for_their_own_approval(env):
    client, db, _idir, kids = env
    page = client.get(f"{EDIT}").text
    card = page[page.index('id="sec-ctp"'):]
    assert "Approving is\n    done once on Common Tactical Picture" in card or \
           "done once on Common Tactical Picture" in card
    assert f"approve('{kids[0]['section']}'" not in card
    assert "written" in card
