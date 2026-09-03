"""My Team: personalisation with no account and nothing sent anywhere.

The site is static, so the build cannot know whose browser this is. Every
team's card ships and the client reveals one. These tests pin the two halves
of that bargain: the build must contain a card per team and leak nothing,
and the script must degrade to the unpersonalised page whenever the stored
value is missing, stale, or unreadable.
"""
from __future__ import annotations

import re

import pytest

import leaguepage.issue_builder as ib
import leaguepage.matchup_packet as mp
from leaguepage.config import STATIC_DIR, get_league
from leaguepage.site_build import audit_output, build_site
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
    with Storage(tmp_path / "t.sqlite3") as s:
        populate_season(s, DISCO, teams=12, weeks_played=6, current_week=6,
                        season=SEASON, seed=3)
        populate_season(s, SURFEIT, teams=10, weeks_played=6, current_week=6,
                        season=SEASON, seed=9)
        build_site(s, out_dir=tmp_path / "dist",
                   published_dir=tmp_path / "published",
                   editorial_dir=tmp_path / "editorial")
    return tmp_path / "dist"


def _home(built, slug="disco"):
    return (built / slug / "index.html").read_text(encoding="utf-8")


def test_every_team_ships_a_card_and_they_all_start_hidden(built):
    """A reader who never picks a team sees exactly the league-wide site
    that existed before."""
    home = _home(built)
    cards = re.findall(r'<div class="card" data-team="([^"]+)"[^>]*>', home)
    assert len(cards) == 12
    for m in re.finditer(r'<div class="card" data-team="[^"]+"[^>]*>', home):
        assert "hidden" in m.group(0)


def test_the_league_key_is_scoped_so_two_leagues_do_not_collide():
    js = (STATIC_DIR / "myteam.js").read_text(encoding="utf-8")
    assert '"leaguepage:myteam:" + league' in js


def test_the_page_declares_its_league_for_the_script(built):
    for slug in ("disco", "surfeit"):
        assert f'data-league="{slug}"' in _home(built, slug)


def test_the_nav_shortcut_ships_hidden(built):
    """It is revealed by script only once a team is chosen, so it never
    renders for a reader without JavaScript or for a crawler."""
    nav = re.search(r'aria-label="League navigation".*?</nav>', _home(built), re.S).group(0)
    shortcut = re.search(r"<a[^>]*data-myteam-nav[^>]*>", nav).group(0)
    assert "hidden" in shortcut
    assert "__SLUG__" in shortcut


def test_a_stale_slug_falls_back_rather_than_breaking():
    """A team renamed between visits changes its slug. The stored value is
    never trusted."""
    js = (STATIC_DIR / "myteam.js").read_text(encoding="utf-8")
    assert "function known(slug)" in js
    assert "known(slug)" in js


def test_blocked_storage_never_throws():
    """A private window, or a browser set to block site data, must leave the
    page working rather than erroring on load."""
    js = (STATIC_DIR / "myteam.js").read_text(encoding="utf-8")
    reads = js.count("localStorage")
    assert reads >= 3
    assert js.count("try {") >= 2 and js.count("catch") >= 2


def test_no_account_no_network_no_tracking():
    js = (STATIC_DIR / "myteam.js").read_text(encoding="utf-8")
    for banned in ("fetch(", "XMLHttpRequest", "navigator.sendBeacon",
                   "document.cookie", "gtag", "analytics"):
        assert banned not in js, banned


def test_the_choice_can_be_cleared_from_the_page_that_set_it(built):
    home = _home(built)
    assert "data-forget-team" in home
    assert "Change or forget your team" in home


def test_the_card_carries_the_team_briefing_not_a_stub(built):
    """If the module says nothing the personalisation is theatre."""
    home = _home(built)
    start = home.find('<div class="card" data-team=')
    assert start >= 0, "no team card rendered"
    body = home[start:home.find('<div class="card" data-team=', start + 10)]
    for label in ("Next", "Where they stand", "Strength", "Concern"):
        assert f"<dt>{label}</dt>" in body, label


def test_personalisation_does_not_displace_the_league_wide_hierarchy(built):
    """The lead story stays first. This supplements editorial judgment; it
    does not build an algorithmic bubble."""
    home = _home(built)
    lead = home.find('class="card lead"')
    mine = home.find('class="module myteam"')
    assert 0 < lead < mine


def test_every_team_is_still_reachable_without_choosing(built):
    home = _home(built)
    strip = re.search(r'<p class="teamstrip">.*?</p>', home, re.S).group(0)
    assert len(re.findall(r"<a ", strip)) == 12


def test_the_personalisation_markup_leaks_nothing(built):
    assert audit_output(built, public_names=[]) == []
