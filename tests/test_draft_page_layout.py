"""The Draft page is a data page and is laid out like one.

It used to render as a 52rem article: a data-heavy page stranded in the
middle of a wide monitor, with Biggest Reaches and Biggest Steals squeezed
into narrow cards that wrapped every entry onto four lines. These pin the
structure that replaced it -- a wide container the prose pages do not get,
a facts strip instead of a status sentence, reaches and steals as scannable
lists with room to sit side by side, team tables that share the width, and
every table still inside a focusable scroll wrapper.
"""
from __future__ import annotations

import re

import pytest

import leaguepage.draft_value as dv_mod
from leaguepage.storage import Storage

from test_site_build import _build, _publish_minimal, site_env, SEASON, TEST_DISCO  # noqa: F401


def _page(tmp, slug="disco"):
    return (tmp / "dist" / slug / "draft" / "index.html").read_text(encoding="utf-8")


def _css(tmp, slug="disco"):
    return (tmp / "dist" / "assets" / f"{slug}.css").read_text(encoding="utf-8")


# ------------------------------------------------------------- the container

def test_the_draft_page_takes_the_wide_treatment(site_env):
    db, tmp = site_env
    _build(db, tmp)
    assert '<main id="content" class="wide">' in _page(tmp)
    css = _css(tmp)
    assert "main.wide" in css and "max-width:84rem" in css.replace(" ", "")


def test_prose_pages_keep_the_reading_measure(site_env):
    """Widening the draft must not widen everything."""
    db, tmp = site_env
    _publish_minimal(db, tmp, TEST_DISCO, "week-01", "# The Lowdown\n\nWords.\n")
    _build(db, tmp)
    for rel in ("index.html", "standings/index.html", f"{SEASON}/week-01/index.html"):
        html = (tmp / "dist" / "disco" / rel).read_text(encoding="utf-8")
        assert '<main id="content">' in html, rel
    css = _css(tmp)
    assert re.search(r"main\s*\{[^}]*max-width:\s*52rem", css)


def test_long_prose_inside_the_wide_page_is_capped(site_env):
    db, tmp = site_env
    _build(db, tmp)
    html = _page(tmp)
    assert 'class="meta measure" id="how"' in html
    assert ".wide .measure" in _css(tmp)


# ------------------------------------------------------------- the header

def test_the_summary_is_a_strip_of_labelled_facts(site_env):
    db, tmp = site_env
    _build(db, tmp)
    html = _page(tmp)
    facts = html[html.index('<dl class="facts"'):html.index("</dl>")]
    for label in ("Status", "Picks", "Teams", "Rounds", "Format"):
        assert f"<dt>{label}</dt>" in facts, label
    assert "<dd>36 of 36</dd>" in facts
    assert "<dd>12</dd>" in facts


def test_the_recap_is_a_quiet_callout_not_a_floating_link(site_env):
    db, tmp = site_env
    _publish_minimal(db, tmp, TEST_DISCO, "draft", "# The Lowdown\n\nDraft words.\n")
    _build(db, tmp)
    html = _page(tmp)
    assert '<aside class="callout measure">' in html
    assert "Draft Issue" in html and "draft recap" in html
    assert "cta" not in html[html.index("callout"):html.index("</aside>")]


def test_no_recap_means_no_callout(site_env):
    db, tmp = site_env
    _build(db, tmp)
    assert "callout" not in _page(tmp)


# ----------------------------------------------------- market deviations
#
# The synthetic draft carries no reference ranks, so the real board yields no
# headline deviations at all. These tests hand the page explicit ones --
# deltas of a known size on real picks -- which is also what makes the
# REACH/STEAL rendering assertable.

def _fake_deviations(reaches=2, steals=2, special=0):
    def fake(picks, league_size, **kw):
        step = league_size + 3
        rows = list(picks)
        r = [dict(p, delta=-(step + i * 3), adp=p["pick_no"] + step + i * 3)
             for i, p in enumerate(rows[:reaches])]
        s_ = [dict(p, delta=(step + i * 3), adp=max(1, p["pick_no"] - step - i * 3))
              for i, p in enumerate(rows[reaches:reaches + steals])]
        st = [dict(p, delta=-(2 * league_size + 1), adp=p["pick_no"] + 2 * league_size + 1,
                   position="K")
              for p in rows[reaches + steals:reaches + steals + special]]
        return {"skill_reaches": r, "skill_steals": s_, "special_teams": st}
    return fake


def _deviation_cards(html):
    block = html[html.index('<div class="deviations">'):]
    block = block[:block.index("</section>")]
    return re.findall(r'<div class="card( span)?">', block), block


def test_reaches_and_steals_are_scannable_entries(site_env, monkeypatch):
    db, tmp = site_env
    monkeypatch.setattr(dv_mod, "headline_deviations", _fake_deviations(3, 3))
    _build(db, tmp)
    html = _page(tmp)
    cards, block = _deviation_cards(html)
    assert cards == ["", ""], "reaches and steals, side by side, nothing spanning"
    entries = re.findall(
        r'<li>\s*<span class="player">([^<]+)</span>\s*'
        r'<span class="meta"><a href="#team-([a-z0-9-]+)">[^<]+</a> · pick (\d+) · ref ([\d.]+)</span>\s*'
        r'<span class="verdict">(.*?)</span>', block, re.S)
    assert len(entries) == 6, block[:800]
    for _name, slug, _pick, _ref, verdict in entries:
        assert f'id="team-{slug}"' in html, slug
        assert "REACH" in verdict or "STEAL" in verdict, verdict


def test_the_grid_survives_a_missing_list(site_env, monkeypatch):
    """Only steals: one card, and nothing else in the grid to leave an empty
    column beside it."""
    db, tmp = site_env
    monkeypatch.setattr(dv_mod, "headline_deviations", _fake_deviations(0, 2))
    _build(db, tmp)
    cards, block = _deviation_cards(_page(tmp))
    assert cards == [""]
    assert "Biggest Steals" in block and "Biggest Reaches" not in block
    assert "<ol class=\"devlist\">\n      </ol>" not in block


def test_one_headline_list_and_outliers_sit_beside_each_other(site_env, monkeypatch):
    """Reaches plus special teams, no steals: the outliers take the second
    column rather than leaving it empty and spanning beneath."""
    db, tmp = site_env
    monkeypatch.setattr(dv_mod, "headline_deviations", _fake_deviations(2, 0, 1))
    _build(db, tmp)
    cards, block = _deviation_cards(_page(tmp))
    assert cards == ["", " span"]
    assert ".deviations .card.span:nth-child(2) { grid-column:auto; }" in _css(tmp)


def test_special_teams_alone_spans_the_row(site_env, monkeypatch):
    db, tmp = site_env
    monkeypatch.setattr(dv_mod, "headline_deviations", _fake_deviations(0, 0, 1))
    _build(db, tmp)
    cards, block = _deviation_cards(_page(tmp))
    assert cards == [" span"]
    assert "Special Teams Outliers" in block
    assert "outside the reference board" not in block


def test_nothing_to_report_renders_no_deviations_section(site_env, monkeypatch):
    db, tmp = site_env
    monkeypatch.setattr(dv_mod, "headline_deviations",
                        lambda *a, **k: {"skill_reaches": [], "skill_steals": [],
                                         "special_teams": []})
    _build(db, tmp)
    assert "Market Deviations" not in _page(tmp)


def test_the_grid_cannot_overflow_a_narrow_screen(site_env):
    """`minmax(30rem, 1fr)` on a 320px viewport lays a 480px track; the
    `min()` is what keeps the phone from scrolling sideways."""
    db, tmp = site_env
    _build(db, tmp)
    css = _css(tmp).replace(" ", "")
    assert css.count("minmax(min(30rem,100%),1fr)") == 2, "deviations and team grids"


# ----------------------------------------------------------- team-by-team

def test_team_jump_links_are_finger_sized_and_resolve(site_env):
    db, tmp = site_env
    _build(db, tmp)
    html = _page(tmp)
    start = html.index('<nav class="teamstrip jump"')
    strip = html[start:html.index("</nav>", start)]
    links = re.findall(r'href="#team-([a-z0-9-]+)"', strip)
    assert len(links) == 12
    for slug in links:
        assert f'id="team-{slug}"' in html
    assert "min-height:2.75rem" in _css(tmp).replace(" ", "")


def test_team_sections_share_the_width(site_env):
    db, tmp = site_env
    _build(db, tmp)
    html = _page(tmp)
    assert '<div class="teamdraft">' in html
    assert ".teamdraft .card" in _css(tmp) and "min-width:0" in _css(tmp).replace(" ", "")


def test_team_page_links_from_the_draft_resolve(site_env):
    db, tmp = site_env
    _build(db, tmp)
    html = _page(tmp)
    hrefs = re.findall(r'href="\.\./team/([a-z0-9-]+)/index\.html"', html)
    assert len(hrefs) == 12
    for slug in hrefs:
        assert (tmp / "dist" / "disco" / "team" / slug / "index.html").exists(), slug


# ------------------------------------------------------------------ tables

def test_every_draft_table_sits_in_a_focusable_scroll_wrapper(site_env):
    db, tmp = site_env
    _build(db, tmp)
    html = _page(tmp)
    tables = [m.start() for m in re.finditer(r"<table", html)]
    assert len(tables) == 13, "12 team tables and the full board"
    for pos in tables:
        before = html[max(0, pos - 220):pos]
        assert 'class="tablewrap" tabindex="0" role="region"' in before, html[pos - 220:pos]
