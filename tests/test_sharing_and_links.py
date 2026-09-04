"""The site as something you can link to and browse.

Two failures this pins. A page with no outbound link is where a reader
stops, and 82 of 98 pages were that. A page with no metadata pastes into a
group chat as a bare blue URL, and all 98 were that.
"""
from __future__ import annotations

import re

import pytest

import leaguepage.issue_builder as ib
import leaguepage.matchup_packet as mp
from leaguepage.config import SITE_URL, get_league
from leaguepage.site_build import build_site
from leaguepage.storage import Storage

from season import populate_season

DISCO = get_league("disco")
SURFEIT = get_league("surfeit")
SEASON = "2027"


@pytest.fixture
def built(tmp_path, monkeypatch):
    monkeypatch.setattr(ib, "EDITORIAL_DIR", tmp_path / "editorial")
    monkeypatch.setattr(mp, "EDITORIAL_DIR", tmp_path / "editorial")
    monkeypatch.setattr(mp, "load_managers", lambda: {})
    monkeypatch.setattr(mp, "load_coalitions",
                        lambda: {"identities": {}, "coalitions": [], "relationships": []})
    db = tmp_path / "t.sqlite3"
    with Storage(db) as s:
        populate_season(s, DISCO, teams=12, weeks_played=4, current_week=4,
                        season=SEASON, seed=3)
        populate_season(s, SURFEIT, teams=10, weeks_played=4, current_week=4,
                        season=SEASON, seed=9)
        s.upsert_archive_issue(
            league_slug="disco", season="2021", week=4, title="2021 Disco Week 4",
            source_path="archive/disco/a.md", body="One paragraph.\n\nAnother.",
            dating_confidence="high", dating_note="")
        s.upsert_archive_issue(
            league_slug="disco", season="2021", week=5, title="2021 Disco Week 5",
            source_path="archive/disco/b.md", body="One paragraph.\n\nAnother.",
            dating_confidence="high", dating_note="")
        build_site(s, out_dir=tmp_path / "dist",
                   published_dir=tmp_path / "published",
                   editorial_dir=tmp_path / "editorial")
    return tmp_path / "dist"


def _main(path):
    body = path.read_text(encoding="utf-8")
    m = re.search(r"<main[^>]*>(.*?)</main>", body, re.S)
    return m.group(1) if m else ""


def test_no_page_is_a_dead_end(built):
    """An item without somewhere to go is a fact, not a story, and a page
    without somewhere to go is where the reader closes the tab."""
    dead = [str(p.relative_to(built)) for p in sorted(built.rglob("*.html"))
            if p.name != "index.html" or p.parent != built
            if not re.search(r"<a\s[^>]*href=", _main(p))]
    assert dead == [], dead


def test_team_names_link_to_team_pages_on_the_data_routes(built):
    """Standings, Force Flow and the Black Box printed team names as plain
    text, which is what made them terminal."""
    for route in ("standings/index.html", "black-box/index.html",
                  "matchups/index.html", "teams/index.html"):
        main = _main(built / "disco" / route)
        assert re.search(r'href="[^"]*team/[^"]+/index\.html"', main), route


def test_every_page_carries_a_description_and_a_canonical(built):
    for page in sorted(built.rglob("*.html")):
        body = page.read_text(encoding="utf-8")
        rel = page.relative_to(built)
        assert '<meta name="description"' in body, rel
        assert '<link rel="canonical"' in body, rel
        assert 'property="og:title"' in body, rel
        assert SITE_URL in body, rel


def test_descriptions_are_specific_to_the_page(built):
    """A preview card that says the same thing on every page is the bare
    URL with extra steps."""
    seen = {}
    for page in sorted(built.rglob("*.html")):
        body = page.read_text(encoding="utf-8")
        m = re.search(r'<meta name="description" content="([^"]*)"', body)
        assert m, page
        seen.setdefault(m.group(1), []).append(str(page.relative_to(built)))
    repeats = {d: p for d, p in seen.items() if len(p) > 1}
    assert not repeats, repeats


def test_preseason_publishes_no_standings_order(tmp_path, monkeypatch):
    """Every team is 0-0 on 0 points, so the sort is a roster_id tiebreak.
    Publishing it as '#11' told one manager he was eleventh in a league
    where nobody had played."""
    monkeypatch.setattr(ib, "EDITORIAL_DIR", tmp_path / "editorial")
    monkeypatch.setattr(mp, "EDITORIAL_DIR", tmp_path / "editorial")
    monkeypatch.setattr(mp, "load_managers", lambda: {})
    monkeypatch.setattr(mp, "load_coalitions",
                        lambda: {"identities": {}, "coalitions": [], "relationships": []})
    db = tmp_path / "p.sqlite3"
    with Storage(db) as s:
        populate_season(s, DISCO, teams=12, weeks_played=0, season=SEASON)
        populate_season(s, SURFEIT, teams=10, weeks_played=0, season=SEASON)
        build_site(s, out_dir=tmp_path / "dist",
                   published_dir=tmp_path / "published",
                   editorial_dir=tmp_path / "editorial")
    out = tmp_path / "dist"
    standings = _main(out / "disco" / "standings" / "index.html")
    assert "no order to publish" in standings
    teams = _main(out / "disco" / "teams" / "index.html")
    assert "model board #" in teams
    assert re.search(r"0-0 \W{0,3}#\d", teams) is None, "a rank off no games"


def test_archive_issues_link_to_their_neighbours(built):
    a = _main(built / "disco" / "archive" / "a1" / "index.html")
    b = _main(built / "disco" / "archive" / "a2" / "index.html")
    assert "a2/index.html" in a, "no link forward from the first issue"
    assert "a1/index.html" in b, "no link back from the second issue"
    for main in (a, b):
        assert "All issues" in main


def test_sortable_headers_keep_their_column_semantics():
    """role='button' on a th overrides its columnheader role, which voids
    every scope='col' association in the column and makes aria-sort
    invalid."""
    from leaguepage.config import STATIC_DIR
    js = (STATIC_DIR / "sortable.js").read_text(encoding="utf-8")
    assert 'setAttribute("role", "button")' not in js
    assert 'createElement("button")' in js


def test_the_front_door_has_one_h1_and_real_landmarks(built):
    """dist/index.html is the site's front door and had two h1s, no main,
    and no skip link."""
    body = (built / "index.html").read_text(encoding="utf-8")
    assert body.count("<h1") == 1
    assert "<main" in body
    assert 'class="skip"' in body
    assert "Disco Chat" in body and "The Surfeit" in body


def test_editorial_tables_scroll_inside_themselves(built):
    """Tables arrive as raw HTML from published prose and were the only
    thing on the site that scrolled the whole page sideways."""
    css = (built / "assets" / "disco.css").read_text(encoding="utf-8")
    # Scoped to .prose. The old selector also caught every table the site
    # builds itself, took the scrolling away from the .tablewrap wrapper
    # that was supposed to do it, and made those tables display:block --
    # which stops a table being a table for assistive technology.
    assert ".prose table { display:block; overflow-x:auto" in css
    page = (built / "disco" / "standings" / "index.html").read_text(encoding="utf-8")
    assert 'class="tablewrap" tabindex="0" role="region" aria-label=' in page


def test_there_is_a_print_stylesheet(built):
    """This is an archive of a newspaper; printing one produced blank
    paper, because browsers drop background colours."""
    css = (built / "assets" / "disco.css").read_text(encoding="utf-8")
    assert "@media print" in css
    assert "background:#fff" in css
