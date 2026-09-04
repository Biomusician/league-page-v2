"""The stylesheet is a file, not 98 copies of itself.

It used to be inlined into every document: 728KB of the build's 1.9MB of
HTML, and 74% of the bytes on the smallest pages, re-sent on every click
through a fifty-five issue archive.

The risk in extracting it is silent and total — one wrong relative path and
the whole site renders unstyled while every test that checks for text still
passes. So these check the link resolves from every depth, and that the file
it points at actually contains the theme.
"""
from __future__ import annotations

import re

import pytest

import leaguepage.issue_builder as ib
import leaguepage.matchup_packet as mp
from leaguepage.config import get_league
from leaguepage.site_build import audit_output, build_site
from leaguepage.storage import Storage

from season import populate_season

SEASON = "2027"


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("css")
    import unittest.mock as m
    with m.patch.object(ib, "EDITORIAL_DIR", tmp / "editorial"), \
            m.patch.object(mp, "EDITORIAL_DIR", tmp / "editorial"), \
            m.patch.object(mp, "load_managers", lambda: {}), \
            m.patch.object(mp, "load_coalitions",
                           lambda: {"identities": {}, "coalitions": [],
                                    "relationships": []}):
        with Storage(tmp / "t.sqlite3") as s:
            populate_season(s, get_league("disco"), teams=12, weeks_played=5,
                            current_week=5, season=SEASON, seed=3)
            populate_season(s, get_league("surfeit"), teams=10, weeks_played=5,
                            current_week=5, season=SEASON, seed=9)
            build_site(s, out_dir=tmp / "dist", published_dir=tmp / "published",
                       editorial_dir=tmp / "editorial")
    return tmp / "dist"


def test_one_stylesheet_per_theme_is_written(built):
    for slug in ("disco", "surfeit"):
        css = built / "assets" / f"{slug}.css"
        assert css.exists(), slug
        assert len(css.read_text(encoding="utf-8")) > 5000, slug


def test_the_two_themes_are_actually_different(built):
    """Each league's page ground comes from its own insignia: the 606th Air
    Control Squadron patch's indigo field for Disco, the Skunk Works black
    for The Surfeit. Pinning the ground colour rather than an accent keeps
    this honest about the one thing a reader cannot miss."""
    a = (built / "assets" / "disco.css").read_text(encoding="utf-8")
    b = (built / "assets" / "surfeit.css").read_text(encoding="utf-8")
    assert a != b
    assert "#15142c" in a, "Disco lost the 606 ACS indigo ground"
    assert "#0a0b0e" in b, "The Surfeit lost the Skunk Works black ground"
    # and neither may borrow the other's ground
    assert "#0a0b0e" not in a and "#15142c" not in b


def test_the_link_resolves_from_every_depth(built):
    """One wrong relative path renders the whole site unstyled while every
    test that checks for text still passes."""
    missing = []
    for page in sorted(built.rglob("*.html")):
        for href in re.findall(r'<link rel="stylesheet" href="([^"]+)"', page.read_text(
                encoding="utf-8")):
            if not (page.parent / href).resolve().exists():
                missing.append(f"{page.relative_to(built)} -> {href}")
    assert missing == [], missing[:10]


def test_every_league_page_links_a_stylesheet(built):
    """A page that links none is a page that renders as raw markup."""
    unstyled = []
    for page in sorted(built.rglob("*.html")):
        rel = page.relative_to(built).as_posix()
        if rel == "index.html":
            continue        # the league-select page carries its own 25 lines
        body = page.read_text(encoding="utf-8")
        if '<link rel="stylesheet"' not in body:
            unstyled.append(rel)
    assert unstyled == [], unstyled[:10]


def test_the_page_no_longer_carries_the_whole_stylesheet(built):
    home = (built / "disco" / "index.html").read_text(encoding="utf-8")
    inline = "".join(re.findall(r"<style>(.*?)</style>", home, re.S))
    assert len(inline) < 2500, len(inline)
    # and the theme really did move out, not get dropped
    assert "--" not in inline or "#14181d" not in inline


def test_the_smallest_pages_got_much_smaller(built):
    """These were 63-74% stylesheet."""
    page = built / "surfeit" / "archive" / "index.html"
    assert page.stat().st_size < 6000, page.stat().st_size


def test_a_stylesheet_is_audited_for_credentials_but_not_for_names(built):
    """A .css file has no prose in it, and `border:3px double` is not a
    person. Credential shapes still apply to every audited file."""
    from leaguepage.privacy import PRIVATE_PATTERNS

    css = (built / "assets" / "disco.css")
    assert "double" in css.read_text(encoding="utf-8")
    assert audit_output(built, public_names=[]) == []

    css.write_text(css.read_text(encoding="utf-8")
                   + "\n/* " + "AKIA" + "A" * 16 + " */\n", encoding="utf-8")
    found = audit_output(built, public_names=[])
    assert any("AWS access key" in v for v in found), found
    assert any(lab == "AWS access key" for _p, lab in PRIVATE_PATTERNS)
