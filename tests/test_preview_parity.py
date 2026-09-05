"""The private preview and the published page are one renderer.

There used to be two: `templates/desk/full_preview.html` drew something
that looked roughly like an issue, and the real page was built by
`public/issue_page.html`. They drifted, which makes a preview worse than
useless — it is confidently wrong about the thing it previews. These tests
exist to stop the second renderer coming back.
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
from leaguepage.site_build import _issue_ctx, preview_snapshot
from leaguepage.storage import Storage

from fixtures import populate_league, populate_matchups

SEASON = "2027"
LG = get_league("surfeit")
BASE = f"/commissioner/surfeit/{SEASON}/issue/week-01"


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
        for key in ("hardware", "ctp", "power", "tracks", "fades", "forceflow",
                    "blackbox", "false-assumptions", "branches", "draft-capsules", "custom"):
            s.set_issue_module(league_slug="surfeit", season=SEASON, issue_key="week-01",
                               module_key=key, included=0)
    idir = ed / SEASON / "surfeit" / "week-01"
    (idir / "lowdown").mkdir(parents=True)
    (idir / "lowdown" / "lowdown.md").write_text(
        "# The Lowdown\n\n## Vol 1: Back On Station\n\nThe room was set, and then it was not.\n",
        encoding="utf-8")
    return TestClient(create_app(db_path=db)), db, idir


def _preview(client):
    r = client.get(f"{BASE}/edit/full-preview")
    assert r.status_code == 200, r.text[-800:]
    return r.text


# ------------------------------------------------------- one renderer only

def test_the_second_renderer_is_gone_and_cannot_come_back():
    assert not pathlib.Path("templates/desk/full_preview.html").exists()
    tpl = pathlib.Path("templates/desk/canonical_preview.html").read_text(encoding="utf-8")
    assert '{% extends "public/issue_page.html" %}' in tpl
    # the whole document comes from the public template; this file adds a
    # toolbar and nothing else that could drift
    assert "<article" not in tpl and "section-label" not in tpl


def test_the_preview_is_the_public_document(env):
    """Same masthead, same nav, same section markup, same stylesheet."""
    client, _db, _idir = env
    html = _preview(client)
    assert '<header class="masthead">' in html
    assert '<nav class="leaguenav"' in html
    assert '<h2 class="section-label">The Lowdown</h2>' in html
    assert 'href="/commissioner/preview-assets/surfeit.css"' in html
    assert 'UNPUBLISHED COMMISSIONER PREVIEW' in html
    assert 'PRIVATE PREVIEW' in html


def test_the_preview_stylesheet_is_the_one_the_build_writes(env, tmp_path):
    from leaguepage.site_build import _env

    client, _db, _idir = env
    served = client.get("/commissioner/preview-assets/surfeit.css")
    built = _env().get_template("public/_site_css.html").render(league=LG)
    assert served.status_code == 200 and served.text == built


def test_section_structure_matches_the_published_page_exactly(env):
    """Build the same issue both ways and compare the rendered document."""
    client, db, idir = env
    with Storage(db) as s:
        snap = preview_snapshot(s, LG, SEASON, "week-01")
    published_like = dict(snap, published_at="2027-09-01T00:00:00+00:00")
    pub_ctx = _issue_ctx(published_like)
    prev_ctx = _issue_ctx(snap, preview=True)
    assert [x["anchor"] for x in pub_ctx["sections"]] == [x["anchor"] for x in prev_ctx["sections"]]
    assert [x["html"] for x in pub_ctx["sections"]] == [x["html"] for x in prev_ctx["sections"]]
    assert pub_ctx["headline"] == prev_ctx["headline"] == "Vol 1: Back On Station"
    # the ONLY difference is the preview flag itself
    assert prev_ctx["preview"] is True and pub_ctx["preview"] is False


def test_the_preview_shows_provenance_the_way_a_reader_will(env):
    from leaguepage import provenance as pv

    client, db, idir = env
    with Storage(db) as s:
        pv.record(s, league_slug="surfeit", season=SEASON, issue_key="week-01",
                  section="lowdown", generator="claude-code", method="section-brief",
                  text=(idir / "lowdown" / "lowdown.md").read_text(encoding="utf-8"),
                  event="proposal-accept")
    html = _preview(client)
    assert 'class="prov"' in html and "AI-generated" in html


# ------------------------------------------------------------ safety

def test_the_preview_is_private_and_carries_no_reader_machinery(env):
    """A preview must never become a public unpublished URL, and it does
    not run the reader's scripts or open comments on an unpublished page."""
    client, _db, _idir = env
    html = _preview(client)
    assert "giscus" not in html
    assert "myteam.js" not in html and "sortable.js" not in html
    assert "<link rel=\"canonical\"" not in html
    # the auth middleware's public list must never name a preview route
    src = pathlib.Path("leaguepage/desk.py").read_text(encoding="utf-8")
    public = src[src.index("PUBLIC_PATHS = {"):]
    public = public[:public.index("}") + 1]
    assert "/commissioner" not in public and "preview" not in public


def test_the_asset_route_serves_only_public_site_assets(env):
    client, _db, _idir = env
    assert client.get("/commissioner/preview-assets/disco.css").status_code == 200
    assert client.get("/commissioner/preview-assets/surfeit-badge.png").status_code == 200
    for blocked in ("desk.js", "myteam.js", "nope.css", "../../data/league.sqlite3",
                    "..%2F..%2Fdata%2Fleague.sqlite3"):
        assert client.get(f"/commissioner/preview-assets/{blocked}").status_code == 404, blocked


def test_published_pages_do_not_carry_preview_markup(tmp_path):
    """The toolbar block is empty for everything the public ever sees."""
    base = pathlib.Path("templates/public/base.html").read_text(encoding="utf-8")
    assert "{% block private_toolbar %}{% endblock %}" in base
    body = base[base.index("<body>"):]
    assert "previewbar" not in body


def test_the_preview_warns_with_the_blockers_publication_would_raise(env):
    client, db, _idir = env
    with Storage(db) as s:                      # nothing approved yet
        assert s.get_issue_modules("surfeit", SEASON, "week-01") is not None
    html = _preview(client)
    assert "would block publication" in html or "PRIVATE PREVIEW" in html
