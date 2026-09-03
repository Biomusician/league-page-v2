"""Every internal link in the build must resolve.

This exists because 22 links shipped to production pointing at team pages
that were never built: the draft page used the draft analysis's own team
slug for an href, and the site's team pages live at a different one. Three
slug vocabularies in one codebase is a standing hazard, so the build is
checked rather than the slugs trusted.
"""
from __future__ import annotations

import re
from urllib.parse import unquote, urldefrag

import pytest

import leaguepage.issue_builder as ib
import leaguepage.matchup_packet as mp
from leaguepage.config import get_league
from leaguepage.site_build import build_site
from leaguepage.storage import Storage

from season import populate_season

DISCO = get_league("disco")
SURFEIT = get_league("surfeit")
SEASON = "2027"

_HREF = re.compile(r'\b(?:href|src)="([^"]+)"')
_ID = re.compile(r'\sid="([^"]+)"')


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("links")
    import unittest.mock as m
    with m.patch.object(ib, "EDITORIAL_DIR", tmp / "editorial"), \
            m.patch.object(mp, "EDITORIAL_DIR", tmp / "editorial"), \
            m.patch.object(mp, "load_managers", lambda: {}), \
            m.patch.object(mp, "load_coalitions",
                           lambda: {"identities": {}, "coalitions": [],
                                    "relationships": []}):
        with Storage(tmp / "t.sqlite3") as s:
            populate_season(s, DISCO, teams=12, weeks_played=6, current_week=6,
                            season=SEASON, seed=3)
            populate_season(s, SURFEIT, teams=10, weeks_played=6, current_week=6,
                            season=SEASON, seed=9)
            s.upsert_archive_issue(
                league_slug="disco", season="2021", week=4, title="2021 Disco Week 4",
                source_path="archive/disco/a.md", body="One.\n\nTwo.",
                dating_confidence="high", dating_note="")
            build_site(s, out_dir=tmp / "dist", published_dir=tmp / "published",
                       editorial_dir=tmp / "editorial")
    return tmp / "dist"


def _targets(page, out):
    """Every internal href/src on one page, as (raw, resolved path, fragment)."""
    body = page.read_text(encoding="utf-8")
    for raw in _HREF.findall(body):
        if raw.startswith(("http://", "https://", "mailto:", "data:", "//")):
            continue
        path, frag = urldefrag(unquote(raw))
        if not path:
            yield raw, page, frag          # same-page anchor
            continue
        yield raw, (page.parent / path).resolve(), frag


def test_every_internal_link_resolves_to_a_file(built):
    broken = []
    for page in sorted(built.rglob("*.html")):
        for raw, target, _frag in _targets(page, built):
            if not target.exists():
                broken.append(f"{page.relative_to(built)} -> {raw}")
    assert broken == [], broken[:20]


def test_every_anchor_link_finds_its_element(built):
    """A link to #foo that lands on a page with no id="foo" is a link to the
    top of the page wearing a promise."""
    ids: dict = {}
    missing = []
    for page in sorted(built.rglob("*.html")):
        for raw, target, frag in _targets(page, built):
            if not frag or not target.exists() or target.suffix != ".html":
                continue
            if target not in ids:
                ids[target] = set(_ID.findall(target.read_text(encoding="utf-8")))
            if frag not in ids[target]:
                missing.append(f"{page.relative_to(built)} -> {raw}")
    assert missing == [], missing[:20]


def test_every_asset_referenced_by_the_build_exists(built):
    missing = []
    for page in sorted(built.rglob("*.html")):
        for raw, target, _ in _targets(page, built):
            if raw.endswith((".js", ".css", ".png", ".jpg", ".jpeg", ".svg")) \
                    and not target.exists():
                missing.append(f"{page.relative_to(built)} -> {raw}")
    assert missing == [], missing


def test_no_page_links_to_a_slug_from_a_different_vocabulary(built):
    """The draft page, the matchup anchors and the team pages each derive a
    slug. Only one of them is a URL."""
    real = {p.parent.name for p in built.glob("disco/team/*/index.html")}
    assert real
    bad = []
    for page in sorted(built.glob("disco/**/*.html")):
        for raw, _target, _ in _targets(page, built):
            m = re.search(r"team/([^/]+)/index\.html$", raw)
            if m and m.group(1) not in real:
                bad.append(f"{page.relative_to(built)} -> {raw}")
    assert bad == [], bad[:20]
